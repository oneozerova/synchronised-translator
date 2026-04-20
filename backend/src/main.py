import asyncio
import base64
import json
import re
import struct
import websockets
from io import BytesIO
from websockets.exceptions import ConnectionClosed
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
import wave
from src.settings import settings
from src.translator_llm import TranslationSession

app = FastAPI()

stt_url = settings.stt_url
tts_url = settings.tts_url
enable_tts = settings.enable_tts
tts_required = settings.tts_required

print(f"[Proxy] STT URL: {stt_url}")
print(f"[Proxy] TTS URL: {tts_url}")
print(f"[Proxy] TTS enabled: {enable_tts}, required: {tts_required}")


MAX_CHUNK_WORDS = 10
PUNCTUATION_RE = re.compile(r"[.!?;:…]$")
STOP_WORDS = {
    "and",
    "but",
    "or",
    "so",
    "because",
    "that",
    "which",
    "who",
    "when",
    "while",
    "if",
    "then",
    "for",
    "to",
    "from",
    "with",
    "without",
    "at",
    "in",
    "on",
    "by",
    "of",
}


def norm_word(word: str) -> str:
    return word.lower().strip(".,!?…;:-_\"'()[]{}")


def find_anchor(committed_words: list[str], all_words: list[str]) -> int:
    norm_all = [norm_word(w) for w in all_words]
    norm_committed = [norm_word(w) for w in committed_words]
    max_tail = min(8, len(norm_committed))
    for tail_len in range(max_tail, 0, -1):
        tail = norm_committed[-tail_len:]
        for i in range(len(norm_all) - tail_len + 1):
            if norm_all[i:i + tail_len] == tail:
                return i + tail_len
    return 0


def extract_new_words(committed_words: list[str], stable_words: list[str]) -> list[str]:
    if not stable_words:
        return []
    if not committed_words:
        return stable_words

    anchor = find_anchor(committed_words, stable_words)
    if anchor < len(stable_words):
        return stable_words[anchor:]
    return []


class ChunkAccumulator:
    def __init__(self, max_words: int, stop_words: set[str]):
        self.max_words = max_words
        self.stop_words = stop_words
        self.buffer: list[str] = []

    def _should_flush(self, word: str) -> bool:
        if len(self.buffer) >= self.max_words:
            return True
        if PUNCTUATION_RE.search(word):
            return True
        return norm_word(word) in self.stop_words

    def _flush(self) -> str:
        chunk = " ".join(self.buffer).strip()
        self.buffer = []
        return chunk

    def push_words(self, words: list[str]) -> list[str]:
        chunks: list[str] = []
        for word in words:
            self.buffer.append(word)
            if self._should_flush(word):
                flushed = self._flush()
                if flushed:
                    chunks.append(flushed)
        return chunks


def build_silent_wav_base64(duration_sec: float = 1.0, sample_rate: int = 24000) -> str:
    frame_count = int(duration_sec * sample_rate)
    pcm_silence = struct.pack("<" + "h" * frame_count, *([0] * frame_count))
    buff = BytesIO()
    with wave.open(buff, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_silence)
    return base64.b64encode(buff.getvalue()).decode("ascii")


@app.websocket("/ws")
async def websocket_endpoint(client_ws: WebSocket):
    await client_ws.accept()
    print(f"[Proxy] Client connected: {client_ws.client}")

    # Подключаемся только к STT.
    try:
        print(f"[Proxy] STT URL: {stt_url}")
        server_ws = await websockets.connect(
            stt_url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5
        )
        print("[Proxy] Connected to STT server")
    except Exception as e:
        print(f"[Proxy] Failed to connect to STT: {e}")
        await client_ws.close(code=1011)
        return

    tts_ws = None
    if enable_tts:
        try:
            tts_ws = await websockets.connect(
                tts_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                max_size=None,
            )
            print("[Proxy] Connected to TTS server")
        except Exception as e:
            print(f"[Proxy] Failed to connect to TTS: {e}")
            if tts_required:
                await server_ws.close()
                await client_ws.close(code=1011)
                return
            print("[Proxy] Continue without TTS (STT + translation only)")

    translation_session = TranslationSession()
    source_committed_words: list[str] = []
    translated_chunks: list[str] = []
    accumulator = ChunkAccumulator(MAX_CHUNK_WORDS, STOP_WORDS)
    sequence_state = {"value": 0}
    tts_state = {"started": False}
    shutdown_flag = asyncio.Event()

    async def backend_to_stt():
        """Client → STT"""
        try:
            while not shutdown_flag.is_set():
                try:
                    msg = await asyncio.wait_for(client_ws.receive(), timeout=30.0)
                    msg_type = msg.get("type")

                    if msg_type == "websocket.disconnect":
                        print("[Proxy] Client disconnected (backend_to_stt)")
                        break

                    if msg_type != "websocket.receive":
                        continue

                    text_payload = msg.get("text")
                    if text_payload is not None:
                        try:
                            control = json.loads(text_payload)
                        except json.JSONDecodeError:
                            continue

                        if control.get("event") == "tts_start":
                            if tts_ws is None:
                                await client_ws.send_text(json.dumps({
                                    "event": "warning",
                                    "message": "TTS disabled or unavailable for this session",
                                }))
                                continue
                            tts_start_payload = {
                                "event": "start",
                                "ref_audio": control.get("ref_audio") or build_silent_wav_base64(),
                                "ref_text": control.get("ref_text", ""),
                                "lang": control.get("lang", "Russian"),
                            }
                            await tts_ws.send(json.dumps(tts_start_payload))
                            tts_state["started"] = True
                        continue

                    data = msg.get("bytes")
                    if data is not None:
                        await server_ws.send(data)
                except WebSocketDisconnect:
                    print("[Proxy] Client disconnected (backend_to_stt)")
                    break
                except asyncio.TimeoutError:
                    # Проверяем, не пора ли завершаться
                    if shutdown_flag.is_set():
                        break
                    continue
                except ConnectionClosed:
                    print("[Proxy] STT connection closed (backend_to_stt)")
                    break
        except Exception as e:
            print(f"[Proxy] backend_to_stt unexpected error: {type(e).__name__}: {e}")
        finally:
            shutdown_flag.set()

    async def stt_to_backend():
        """STT → In-process translator → Client"""
        try:
            while not shutdown_flag.is_set():
                try:
                    msg = await asyncio.wait_for(
                        server_ws.recv(),
                        timeout=30.0
                    )
                    print(f"[STT] Raw: {msg}")

                    # 👇 ВАЖНО: STT шлёт текст (JSON строку)
                    if isinstance(msg, str):
                        try:
                            stt_data = json.loads(msg)

                            stable_text = (stt_data.get("stable") or "").strip()
                            if stable_text:
                                stable_words = stable_text.split()
                                new_words = extract_new_words(source_committed_words, stable_words)
                                if new_words:
                                    source_committed_words.extend(new_words)
                                    chunks_to_translate = accumulator.push_words(new_words)
                                    for source_chunk in chunks_to_translate:
                                        translated_chunk = await asyncio.to_thread(
                                            translation_session.translate_chunk,
                                            source_chunk,
                                        )
                                        if translated_chunk:
                                            if tts_ws is not None and not tts_state["started"]:
                                                await tts_ws.send(json.dumps({
                                                    "event": "start",
                                                    "ref_audio": build_silent_wav_base64(),
                                                    "ref_text": "reference",
                                                    "lang": "Russian",
                                                }))
                                                tts_state["started"] = True
                                            translated_chunks.append(translated_chunk)
                                            if tts_ws is not None:
                                                await tts_ws.send(json.dumps({
                                                    "event": "chunk",
                                                    "seq": sequence_state["value"],
                                                    "text": translated_chunk,
                                                }))
                                                sequence_state["value"] += 1

                            translated_stable = " ".join(translated_chunks).strip()
                            await client_ws.send_text(json.dumps({
                                "event": "translation",
                                "stable": translated_stable,
                                "pending": "",
                                "chars": len(translated_stable),
                                "speed_logs": stt_data.get("speed_logs", ""),
                            }))

                        except json.JSONDecodeError as e:
                            print(f"Failed to parse STT JSON: {e}, raw: {msg[:100]}")
                            # Fallback: отправляем как есть
                            await client_ws.send_text(msg)
                        except Exception as e:
                            print(f"Translation pipeline error: {e}")
                            # На ошибке — отправляем оригинал
                            await client_ws.send_text(msg)
                    else:
                        # Бинарные данные — как есть
                        await client_ws.send_bytes(msg)

                except WebSocketDisconnect:
                    print("[Proxy] Client disconnected (stt_to_backend)")
                    break
                except asyncio.TimeoutError:
                    if shutdown_flag.is_set():
                        break
                    continue
                except ConnectionClosed:
                    print("[Proxy] STT connection closed (stt_to_backend)")
                    break
                except Exception as e:
                    print(f"[Proxy] Error in stt_to_backend: {e}")
                    # На ошибке — отправляем оригинал
                    if isinstance(msg, str):
                        await client_ws.send_text(msg)
        finally:
            shutdown_flag.set()

    async def tts_to_backend():
        """TTS -> Client (JSON and binary PCM frames)."""
        if tts_ws is None:
            while not shutdown_flag.is_set():
                await asyncio.sleep(0.5)
            return
        try:
            while not shutdown_flag.is_set():
                try:
                    msg = await asyncio.wait_for(tts_ws.recv(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue
                if isinstance(msg, str):
                    await client_ws.send_text(msg)
                else:
                    await client_ws.send_bytes(msg)
        except ConnectionClosed:
            print("[Proxy] TTS connection closed")
        except WebSocketDisconnect:
            print("[Proxy] Client disconnected (tts_to_backend)")
        except Exception as e:
            print(f"[Proxy] Error in tts_to_backend: {e}")
        finally:
            shutdown_flag.set()

    # Запускаем обе задачи
    task1 = asyncio.create_task(backend_to_stt())
    task2 = asyncio.create_task(stt_to_backend())
    task3 = asyncio.create_task(tts_to_backend())

    try:
        await asyncio.wait([task1, task2, task3], return_when=asyncio.FIRST_COMPLETED)
    finally:
        shutdown_flag.set()

        task1.cancel()
        task2.cancel()
        task3.cancel()
        await asyncio.gather(task1, task2, task3, return_exceptions=True)

        if tts_ws is not None:
            try:
                await tts_ws.send(json.dumps({"event": "end"}))
            except Exception:
                pass

        for ws in [server_ws, tts_ws]:
            # Закрываем соединения
            try:
                if ws is not None:
                    await ws.close()
            except Exception as e:
                print(f"[Proxy] Error closing server_ws: {e}")

        try:
            if client_ws.client_state == WebSocketState.CONNECTED:
                await client_ws.close()
        except Exception as e:
            print(f"[Proxy] Error closing client_ws: {e}")

        print("[Proxy] Connection closed, resources cleaned")
