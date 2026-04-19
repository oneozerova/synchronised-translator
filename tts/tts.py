from __future__ import annotations

import asyncio
import base64
import logging
import struct
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import torch
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from faster_qwen3_tts import FasterQwen3TTS

# ─── Настройки ────────────────────────────────────────────────────────────────

MODEL_NAME = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
SAMPLE_RATE = 24000
WINDOW_SIZE = 10                 # текст приходит в сервис кусками по 10 слов
STREAM_CHUNK_SIZE = 4            # размер чанка модели; уменьшай до 2 при хорошей GPU
DTYPE = torch.bfloat16

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tts-server")

_model: FasterQwen3TTS | None = None


@dataclass
class SessionConfig:
    ref_audio_path: str
    ref_text: str
    lang: str


def get_model() -> FasterQwen3TTS:
    global _model
    if _model is None:
        raise RuntimeError("Модель не загружена")
    return _model


def _get_warmup_audio() -> str:
    silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, silence, SAMPLE_RATE)
        return f.name


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    log.info("Загружаем модель %s ...", MODEL_NAME)
    _model = FasterQwen3TTS.from_pretrained(
        MODEL_NAME,
        device="cuda" if torch.cuda.is_available() else "cpu",
        dtype=DTYPE,
    )

    # Небольшой прогрев
    try:
        warmup_path = _get_warmup_audio()
        _model.generate_voice_clone(
            text="warmup warmup warmup",
            language="English",
            ref_audio=warmup_path,
            ref_text="warmup",
            xvec_only=True,
        )
    finally:
        Path(warmup_path).unlink(missing_ok=True)

    log.info("Модель готова")
    yield
    log.info("Выключение сервиса")


app = FastAPI(title="Streaming TTS API", lifespan=lifespan)


# ─── Утилиты ──────────────────────────────────────────────────────────────────

def _decode_base64_wav_to_temp_file(audio_b64: str) -> str:
    audio_bytes = base64.b64decode(audio_b64)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        return f.name


def _to_mono_float32(audio: Any) -> np.ndarray:
    arr = audio
    if hasattr(arr, "detach"):
        arr = arr.detach().cpu().numpy()
    else:
        arr = np.asarray(arr)

    arr = np.squeeze(arr)
    if arr.ndim > 1:
        arr = arr[0]

    if np.issubdtype(arr.dtype, np.integer):
        max_val = np.iinfo(arr.dtype).max
        arr = arr.astype(np.float32) / float(max_val)
    else:
        arr = arr.astype(np.float32)

    return arr


def wav_to_pcm16_bytes(wav_array: Any) -> bytes:
    wav = _to_mono_float32(wav_array)
    wav = np.clip(wav, -1.0, 1.0)
    return (wav * 32767.0).astype(np.int16).tobytes()


def _send_json_sync(loop: asyncio.AbstractEventLoop, websocket: WebSocket, payload: dict[str, Any]) -> None:
    fut = asyncio.run_coroutine_threadsafe(websocket.send_json(payload), loop)
    fut.result()


def _send_bytes_sync(loop: asyncio.AbstractEventLoop, websocket: WebSocket, payload: bytes) -> None:
    fut = asyncio.run_coroutine_threadsafe(websocket.send_bytes(payload), loop)
    fut.result()


def _stream_one_text_chunk(
    model: FasterQwen3TTS,
    loop: asyncio.AbstractEventLoop,
    websocket: WebSocket,
    cfg: SessionConfig,
    seq: int,
    text: str,
) -> None:
    t0 = time.perf_counter()
    part_seq = 0
    first_audio_ms: float | None = None

    try:
        _send_json_sync(loop, websocket, {
            "event": "chunk_begin",
            "seq": seq,
            "words": len(text.split()),
            "text": text,
        })

        stream = model.generate_voice_clone_streaming(
            text=text,
            language=cfg.lang,
            ref_audio=cfg.ref_audio_path,
            ref_text=cfg.ref_text,
            xvec_only=True,
            chunk_size=STREAM_CHUNK_SIZE,
        )

        for item in stream:
            if isinstance(item, tuple):
                audio = item[0]
                if len(item) > 1 and isinstance(item[1], (int, float)):
                    _sr = int(item[1])
                else:
                    _sr = SAMPLE_RATE
            elif isinstance(item, dict):
                audio = item.get("audio") or item.get("wav") or item.get("waveform")
                _sr = int(item.get("sample_rate", SAMPLE_RATE))
            else:
                audio = item
                _sr = SAMPLE_RATE

            if audio is None:
                continue

            pcm = wav_to_pcm16_bytes(audio)
            if first_audio_ms is None:
                first_audio_ms = (time.perf_counter() - t0) * 1000.0

            # binary frame:
            # [u32 chunk_seq][u32 part_seq][u32 pcm_len][pcm bytes]
            frame = struct.pack("<III", seq, part_seq, len(pcm)) + pcm
            _send_bytes_sync(loop, websocket, frame)
            part_seq += 1

    except Exception as e:
        try:
            _send_json_sync(loop, websocket, {
                "event": "error",
                "scope": "chunk",
                "seq": seq,
                "message": str(e),
            })
        except Exception:
            pass
        raise
    finally:
        elapsed = time.perf_counter() - t0
        try:
            _send_json_sync(loop, websocket, {
                "event": "chunk_end",
                "seq": seq,
                "parts": part_seq,
                "elapsed": round(elapsed, 3),
                "first_audio_ms": round(first_audio_ms, 1) if first_audio_ms is not None else None,
            })
        except Exception:
            pass


# ─── WebSocket API ────────────────────────────────────────────────────────────

@app.websocket("/api/generate")
async def ws_generate(websocket: WebSocket):
    await websocket.accept()
    loop = asyncio.get_running_loop()
    model = get_model()

    cfg: SessionConfig | None = None
    chunk_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=16)

    async def receiver() -> None:
        nonlocal cfg
        try:
            while True:
                msg = await websocket.receive_json()
                event = msg.get("event")

                if event == "start":
                    if cfg is not None:
                        await websocket.send_json({"event": "error", "message": "start already received"})
                        continue

                    ref_audio_b64 = msg["ref_audio"]
                    ref_text = msg.get("ref_text", "")
                    lang = msg.get("lang", "Russian")

                    ref_audio_path = _decode_base64_wav_to_temp_file(ref_audio_b64)
                    cfg = SessionConfig(
                        ref_audio_path=ref_audio_path,
                        ref_text=ref_text,
                        lang=lang,
                    )

                    await websocket.send_json({
                        "event": "started",
                        "sample_rate": SAMPLE_RATE,
                        "window_size": WINDOW_SIZE,
                        "stream_chunk_size": STREAM_CHUNK_SIZE,
                    })

                elif event == "chunk":
                    if cfg is None:
                        await websocket.send_json({"event": "error", "message": "send start first"})
                        continue

                    seq = int(msg["seq"])
                    text = str(msg["text"]).strip()
                    if text:
                        await chunk_queue.put({"seq": seq, "text": text})

                elif event == "end":
                    break

                else:
                    await websocket.send_json({"event": "error", "message": f"unknown event: {event}"})

        except WebSocketDisconnect:
            log.info("Клиент отключился")
        finally:
            await chunk_queue.put(None)

    async def worker() -> None:
        nonlocal cfg
        while True:
            item = await chunk_queue.get()
            if item is None:
                break
            if cfg is None:
                continue
            await asyncio.to_thread(
                _stream_one_text_chunk,
                model,
                loop,
                websocket,
                cfg,
                item["seq"],
                item["text"],
            )

        try:
            await websocket.send_json({"event": "done"})
        except Exception:
            pass

    try:
        await asyncio.gather(receiver(), worker())
    finally:
        if cfg is not None:
            Path(cfg.ref_audio_path).unlink(missing_ok=True)
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "loaded": _model is not None,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")