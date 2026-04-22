import asyncio
import base64
import json
import logging
import wave
from io import BytesIO

import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
from websockets.exceptions import ConnectionClosed

from src.settings import settings

app = FastAPI()
logger = logging.getLogger("backend-orchestrator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


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


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "stt": f"ws://{settings.stt_host}:{settings.stt_port}/ws",
        "translator": f"ws://{settings.translator_host}:{settings.translator_port}/ws",
        "tts": f"ws://{settings.tts_host}:{settings.tts_port}{settings.tts_path}",
    }


@app.websocket("/ws")
async def websocket_endpoint(client_ws: WebSocket):
    await client_ws.accept()
    logger.info("Client connected: %s", client_ws.client)

    stt_url = f"ws://{settings.stt_host}:{settings.stt_port}/ws"
    translator_url = f"ws://{settings.translator_host}:{settings.translator_port}/ws"
    tts_url = f"ws://{settings.tts_host}:{settings.tts_port}{settings.tts_path}"

    ref_audio_b64 = ""
    ref_text = ""
    tts_lang = settings.tts_default_lang

    translated_stable = ""
    last_stt_stable = ""
    translated_queue: asyncio.Queue[str | None] = asyncio.Queue()
    shutdown_flag = asyncio.Event()

    try:
        stt_ws = await websockets.connect(stt_url, ping_interval=20, ping_timeout=10, close_timeout=5)
        translator_ws = await websockets.connect(translator_url, ping_interval=20, ping_timeout=10, close_timeout=5)
        tts_ws = await websockets.connect(tts_url, ping_interval=20, ping_timeout=10, close_timeout=5)
    except Exception as exc:
        logger.exception("Failed to connect to downstream services")
        await client_ws.send_json({"event": "error", "message": f"downstream unavailable: {exc}"})
        await client_ws.close(code=1011)
        return

    async def client_to_stt():
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
                        continue
                    if event == "session_end":
                        await translated_queue.put(None)
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
            await translated_queue.put(None)

    async def stt_translate_pipeline():
        nonlocal translated_stable, last_stt_stable
        try:
            while not shutdown_flag.is_set():
                msg = await asyncio.wait_for(stt_ws.recv(), timeout=30.0)
                if not isinstance(msg, str):
                    continue

                try:
                    stt_data = json.loads(msg)
                except json.JSONDecodeError:
                    continue

                stable_ru = (stt_data.get("stable") or "").strip()
                pending_ru = (stt_data.get("pending") or "").strip()

                new_ru_chunk = _new_suffix(stable_ru, last_stt_stable)
                if new_ru_chunk:
                    await translator_ws.send(new_ru_chunk)
                    translated_chunk_resp = await asyncio.wait_for(translator_ws.recv(), timeout=30.0)
                    translated_chunk = ""
                    if isinstance(translated_chunk_resp, str):
                        try:
                            translated_chunk = json.loads(translated_chunk_resp).get("translation", "").strip()
                        except json.JSONDecodeError:
                            translated_chunk = translated_chunk_resp.strip()
                    if translated_chunk:
                        translated_stable = f"{translated_stable} {translated_chunk}".strip()
                        await translated_queue.put(translated_chunk)

                last_stt_stable = stable_ru

                await client_ws.send_text(
                    json.dumps(
                        {
                            "event": "translation",
                            "stable": translated_stable,
                            "pending": pending_ru,
                            "source_stable": stable_ru,
                            "source_pending": pending_ru,
                            "chars": len(translated_stable),
                            "speed_logs": stt_data.get("speed_logs", ""),
                            "task": stt_data.get("task", "translate"),
                            "lang": "en",
                        },
                        ensure_ascii=False,
                    )
                )
        except asyncio.TimeoutError:
            logger.info("stt_translate_pipeline timeout")
        except ConnectionClosed:
            logger.info("stt connection closed")
        except Exception:
            logger.exception("stt_translate_pipeline failed")
        finally:
            shutdown_flag.set()
            await translated_queue.put(None)

    async def tts_sender():
        try:
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
            seq = 0
            while not shutdown_flag.is_set():
                text_chunk = await translated_queue.get()
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

    async def tts_to_client():
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
        asyncio.create_task(stt_translate_pipeline()),
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

        for ws in [stt_ws, translator_ws, tts_ws]:
            try:
                await ws.close()
            except Exception:
                pass

        if client_ws.client_state == WebSocketState.CONNECTED:
            try:
                await client_ws.close()
            except Exception:
                pass
