import asyncio
import base64
import json
import logging
import re
import time
import wave
from io import BytesIO

import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
from websockets.exceptions import ConnectionClosed

from src.settings import settings
from src.translator import TranslationSession

app = FastAPI()
logger = logging.getLogger("backend-orchestrator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TTS_BUFFER_TARGET_WORDS = 6
TTS_BUFFER_MIN_WORDS = 3
TTS_BUFFER_IDLE_FLUSH_SEC = 0.8
TTS_BUFFER_MAX_WAIT_SEC = 1.6
TTS_SENTENCE_END_RE = re.compile(r"[.!?…]+[\"')\]]*$")
TTS_CLAUSE_END_RE = re.compile(r"[,;:]+[\"')\]]*$")
TTS_EN_PAUSE_WORDS = {
    "and",
    "but",
    "or",
    "so",
    "because",
    "however",
    "therefore",
    "then",
    "well",
    "now",
}


def _make_silence_wav_base64(duration_sec: float = 0.5, sample_rate: int = 24000) -> str:
    n_frames = max(1, int(duration_sec * sample_rate))
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * n_frames)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _new_suffix(full_text: str, previous_full_text: str) -> str:
    current = full_text.strip()
    previous = previous_full_text.strip()
    if not current:
        return ""
    if not previous:
        return current
    if current.startswith(previous):
        return current[len(previous):].strip()

    overlap = min(len(previous), len(current))
    for size in range(overlap, 0, -1):
        if previous[-size:] == current[:size]:
            return current[size:].strip()
    return current


def _word_count(text: str) -> int:
    return len([w for w in text.split() if w])


def _should_flush_tts_buffer(
    buffer_text: str,
    pending_src: str,
    idle_for_sec: float,
    buffer_age_sec: float,
) -> bool:
    cleaned = buffer_text.strip()
    if not cleaned:
        return False

    words = _word_count(cleaned)
    if words >= TTS_BUFFER_TARGET_WORDS:
        return True
    if words >= TTS_BUFFER_MIN_WORDS and TTS_SENTENCE_END_RE.search(cleaned):
        return True
    if words >= TTS_BUFFER_TARGET_WORDS - 1 and TTS_CLAUSE_END_RE.search(cleaned):
        return True

    last_word = re.sub(r"[^\w]+$", "", cleaned.split()[-1].lower())
    if words >= TTS_BUFFER_TARGET_WORDS - 1 and last_word in TTS_EN_PAUSE_WORDS:
        return True
    if words >= TTS_BUFFER_MIN_WORDS and idle_for_sec >= TTS_BUFFER_IDLE_FLUSH_SEC:
        return True
    if words >= 2 and buffer_age_sec >= TTS_BUFFER_MAX_WAIT_SEC:
        return True
    return False


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "use_translator": settings.use_translator,
        "translator_mode": "in-process" if settings.use_translator else None,
        "stt": settings.stt_websocket_url(),
        "tts": settings.tts_websocket_url(),
    }


@app.websocket("/ws")
async def websocket_endpoint(client_ws: WebSocket):
    await client_ws.accept()
    logger.info("Client connected: %s", client_ws.client)

    stt_url = settings.stt_websocket_url()
    tts_url = settings.tts_websocket_url()

    ref_audio_b64: str = ""
    ref_text: str = ""
    tts_lang: str = settings.tts_default_lang

    # ИСПРАВЛЕНИЕ 1: явный сигнал о том, что session_start получен.
    # tts_sender будет ждать этого события перед отправкой "start" в TTS,
    # иначе возникает гонка: tts_sender стартовал раньше, чем client_to_stt
    # успел прочитать session_start — и TTS получал пустой ref_audio.
    session_ready = asyncio.Event()

    display_stable: str = ""    # накопленный итоговый текст (отправляется клиенту)
    last_stt_stable: str = ""
    tts_buffer: str = ""
    tts_buffer_updated_at = time.monotonic()
    tts_buffer_started_at = time.monotonic()
    tts_text_queue: asyncio.Queue[str | None] = asyncio.Queue()
    shutdown_flag = asyncio.Event()

    translation_session: TranslationSession | None = None
    if settings.use_translator:
        translation_session = TranslationSession(
            api_key=settings.yandex_api_key,
            folder_id=settings.yandex_folder_id,
            source_language=settings.translator_source_language,
            target_language=settings.translator_target_language,
        )

    try:
        stt_ws = await websockets.connect(stt_url, ping_interval=20, ping_timeout=120, close_timeout=5)
        tts_ws = await websockets.connect(tts_url, ping_interval=20, ping_timeout=120, close_timeout=5)
    except Exception as exc:
        logger.exception("Failed to connect to STT/TTS")
        await client_ws.send_json({"event": "error", "message": f"downstream unavailable: {exc}"})
        await client_ws.close(code=1011)
        return

    async def client_to_stt() -> None:
        nonlocal ref_audio_b64, ref_text, tts_lang
        try:
            while not shutdown_flag.is_set():
                packet = await client_ws.receive()
                if packet.get("type") == "websocket.disconnect":
                    break

                text_data = packet.get("text")
                if text_data:
                    try:
                        payload = json.loads(text_data)
                    except json.JSONDecodeError:
                        continue

                    event = payload.get("event")
                    if event == "session_start":
                        ref_audio_b64 = str(payload.get("ref_audio", "")).strip()
                        ref_text = str(payload.get("ref_text", ""))
                        tts_lang = str(payload.get("lang") or settings.tts_default_lang)
                        # ИСПРАВЛЕНИЕ 1: сначала выставляем параметры,
                        # только потом разблокируем tts_sender
                        session_ready.set()
                        logger.info(
                            "session_start: lang=%s ref_text=%r ref_audio_len=%d",
                            tts_lang, ref_text, len(ref_audio_b64),
                        )
                        continue
                    if event == "session_end":
                        logger.info("session_end received from client")
                        continue

                bytes_data = packet.get("bytes")
                if bytes_data:
                    await stt_ws.send(bytes_data)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("client_to_stt failed")
        finally:
            shutdown_flag.set()
            session_ready.set()         # разблокируем tts_sender в случае ошибки
            await tts_text_queue.put(None)

    async def stt_pipeline() -> None:
        nonlocal display_stable, last_stt_stable, tts_buffer, tts_buffer_updated_at, tts_buffer_started_at
        try:
            while not shutdown_flag.is_set():
                msg = await asyncio.wait_for(stt_ws.recv(), timeout=120.0)
                if not isinstance(msg, str):
                    continue

                try:
                    stt_data = json.loads(msg)
                except json.JSONDecodeError:
                    continue

                stable_src = (stt_data.get("text") or "").strip()
                pending_src = (stt_data.get("pending") or "").strip()

                # Берём только новую часть, чтобы не дублировать при кумулятивном STT
                new_piece = _new_suffix(stable_src, last_stt_stable)

                if new_piece:
                    if translation_session is not None:
                        try:
                            tts_chunk = await asyncio.to_thread(
                                translation_session.translate_chunk,
                                new_piece,
                            )
                        except Exception:
                            logger.exception("in-process translation failed")
                            tts_chunk = ""
                        tts_chunk = (tts_chunk or "").strip()
                        if tts_chunk:
                            # ИСПРАВЛЕНИЕ 2: накапливаем переведённый текст
                            if not tts_buffer:
                                tts_buffer_started_at = time.monotonic()
                            display_stable = f"{display_stable} {tts_chunk}".strip()
                            tts_buffer = f"{tts_buffer} {tts_chunk}".strip()
                            tts_buffer_updated_at = time.monotonic()
                    else:
                        # ИСПРАВЛЕНИЕ 2: накапливаем оригинальный текст (не перезаписываем)
                        if not tts_buffer:
                            tts_buffer_started_at = time.monotonic()
                        display_stable = f"{display_stable} {new_piece}".strip()
                        tts_buffer = f"{tts_buffer} {new_piece}".strip()
                        tts_buffer_updated_at = time.monotonic()

                idle_for_sec = time.monotonic() - tts_buffer_updated_at
                buffer_age_sec = time.monotonic() - tts_buffer_started_at
                if _should_flush_tts_buffer(tts_buffer, pending_src, idle_for_sec, buffer_age_sec):
                    await tts_text_queue.put(tts_buffer.strip())
                    tts_buffer = ""
                    tts_buffer_updated_at = time.monotonic()
                    tts_buffer_started_at = tts_buffer_updated_at

                last_stt_stable = stable_src

                # ИСПРАВЛЕНИЕ 2: отправляем display_stable (накопленный), а не stable_src
                # (текущую фразу). Фронт просто отображает пришедшее значение — без
                # собственной логики накопления.
                await client_ws.send_text(
                    json.dumps(
                        {
                            "event": "translation",
                            "stable": display_stable,
                            "pending": pending_src,
                            "source_stable": stable_src,
                            "source_pending": pending_src,
                            "chars": len(display_stable),
                            "speed_logs": stt_data.get("speed_logs", ""),
                            "task": stt_data.get("task", "translate"),
                            "lang": (
                                "en"
                                if stt_data.get("task") == "translate"
                                else stt_data.get("lang", "ru")
                            ),
                            "through_translator": bool(settings.use_translator),
                        },
                        ensure_ascii=False,
                    )
                )
        except asyncio.TimeoutError:
            logger.info("stt pipeline timeout")
        except ConnectionClosed:
            logger.info("stt connection closed")
        except Exception:
            logger.exception("stt_pipeline failed")
        finally:
            if tts_buffer.strip():
                await tts_text_queue.put(tts_buffer.strip())
            shutdown_flag.set()
            await tts_text_queue.put(None)

    async def tts_sender() -> None:
        try:
            # ИСПРАВЛЕНИЕ 1: ждём, пока client_to_stt получит session_start
            # и выставит ref_audio_b64 / ref_text / tts_lang.
            # Без этого ожидания tts_sender уходил вперёд и отправлял "start"
            # с пустым ref_audio — TTS говорил случайным голосом.
            await session_ready.wait()

            if shutdown_flag.is_set():
                return

            await tts_ws.send(
                json.dumps(
                    {
                        "event": "start",
                        "ref_audio": ref_audio_b64 or _make_silence_wav_base64(),
                        "ref_text": ref_text,
                        "lang": tts_lang,
                    }
                )
            )
            logger.info("Sent TTS start: lang=%s ref_audio_len=%d", tts_lang, len(ref_audio_b64))

            seq = 0
            while not shutdown_flag.is_set():
                text_chunk = await tts_text_queue.get()
                if text_chunk is None:
                    break
                await tts_ws.send(json.dumps({"event": "chunk", "seq": seq, "text": text_chunk}))
                seq += 1
            await tts_ws.send(json.dumps({"event": "end"}))
        except ConnectionClosed:
            logger.info("tts sender connection closed")
        except Exception:
            logger.exception("tts_sender failed")
        finally:
            shutdown_flag.set()

    async def tts_to_client() -> None:
        try:
            while not shutdown_flag.is_set():
                msg = await tts_ws.recv()
                if isinstance(msg, bytes):
                    await client_ws.send_bytes(msg)
                    continue
                await client_ws.send_text(msg)
                try:
                    parsed = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if parsed.get("event") == "done":
                    break
        except ConnectionClosed:
            logger.info("tts receiver connection closed")
        except Exception:
            logger.exception("tts_to_client failed")
        finally:
            shutdown_flag.set()

    tasks = [
        asyncio.create_task(client_to_stt()),
        asyncio.create_task(stt_pipeline()),
        asyncio.create_task(tts_sender()),
        asyncio.create_task(tts_to_client()),
    ]

    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        shutdown_flag.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        for ws in [stt_ws, tts_ws]:
            try:
                await ws.close()
            except Exception:
                pass

        if client_ws.client_state == WebSocketState.CONNECTED:
            try:
                await client_ws.close()
            except Exception:
                pass