from __future__ import annotations

import io
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
import torchaudio
import soundfile as sf
import torch
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from df import init_df, enhance # deepfilternet

from faster_qwen3_tts import FasterQwen3TTS

from src.settings import settings

# ─── Модель / поток (как в исходном tts) ─────────────────────────────────────

MODEL_NAME = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
SAMPLE_RATE = 24000
WINDOW_SIZE = 10
STREAM_CHUNK_SIZE = 4
DTYPE = torch.bfloat16
DF_MODEL = None
DF_STATE = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("text-to-speech")

_model: FasterQwen3TTS | None = None


class WebSocketClosedError(RuntimeError):
    """Внутренний сигнал: websocket уже закрыт, поток надо остановить."""


@dataclass
class SessionConfig:
    ref_audio_path: str
    ref_text: str
    lang: str
    delete_ref: bool = True


def get_model() -> FasterQwen3TTS:
    global _model
    if _model is None:
        raise RuntimeError("Модель не загружена")
    return _model


def _get_silence_wav_temp() -> str:
    silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, silence, SAMPLE_RATE)
        return f.name


def denoise_audio(audio_np: np.ndarray, sample_rate: int = 48000) -> np.ndarray:
    """
    Удаление шума с DeepFilterNet2.
    """
    if DF_MODEL is None or DF_STATE is None:
        try:
            from df import init_df
            DF_MODEL, DF_STATE, _ = init_df(model="DeepFilterNet2")
            log.info("DeepFilterNet2 initialized")
        except Exception as e:
            log.error(f"DeepFilterNet2 init failed: {e}")
            return audio_np
    
    try:
        sr = 48000
        if len(audio_np.shape) == 1:
            audio_tensor = torch.from_numpy(audio_np).unsqueeze(0)
        else:
            audio_tensor = torch.from_numpy(audio_np.T).float()
        
        if sample_rate != sr:
            audio_tensor = torchaudio.functional.resample(
                audio_tensor, orig_freq=sample_rate, new_freq=sr
            )
        
        if audio_tensor.shape[0] > 1:
            audio_tensor = audio_tensor.mean(dim=0, keepdim=True)
        
        audio_np = audio_tensor.squeeze(0).numpy().astype(np.float32)
        
        denoised = enhance(DF_MODEL, DF_STATE, audio_np)
        log.debug("Audio denoised")
        return denoised
        
    except Exception as e:
        log.error(f"Denoising failed: {e}")
        return audio_np

@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _model
    log.info("Загружаем модель %s ...", MODEL_NAME)
    _model = FasterQwen3TTS.from_pretrained(
        MODEL_NAME,
        device="cuda" if torch.cuda.is_available() else "cpu",
        dtype=DTYPE,
    )

    warmup_path: str | None = None
    try:
        warmup_path = _get_silence_wav_temp()
        _model.generate_voice_clone(
            text="warmup warmup warmup",
            language="English",
            ref_audio=warmup_path,
            ref_text="warmup",
            xvec_only=True,
        )
    finally:
        if warmup_path is not None:
            Path(warmup_path).unlink(missing_ok=True)

    log.info("Модель готова, default ref: %s", settings.default_ref_wav)
    yield
    log.info("Выключение сервиса")


app = FastAPI(title="Streaming TTS API", lifespan=lifespan)


def _resolve_ref_audio_path(audio_b64: str) -> tuple[str, bool]:
    """
    Возвращает (путь к denoised wav, удалять ли при закрытии сессии).
    """
    b64 = (audio_b64 or "").strip()
    if b64:
        raw = base64.b64decode(b64)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            try:
                audio_np, sr = sf.read(io.BytesIO(raw))
                denoised_np = denoise_audio(audio_np, sr)
                sf.write(f, denoised_np, 48000, format='WAV')
            except Exception as e:
                log.error(f"Denoising failed, using original: {e}")
                f.write(raw)
            return f.name, True
    
    p = settings.default_ref_wav
    if p.is_file():
        return str(p), False
    return _get_silence_wav_temp(), True


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


def _send_sync(loop: asyncio.AbstractEventLoop, coro: Any) -> None:
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        fut.result()
    except Exception as exc:  # socket closed / client disconnected / cancelled
        raise WebSocketClosedError("websocket closed") from exc


def _send_json_sync(loop: asyncio.AbstractEventLoop, websocket: WebSocket, payload: dict[str, Any]) -> None:
    _send_sync(loop, websocket.send_json(payload))


def _send_bytes_sync(loop: asyncio.AbstractEventLoop, websocket: WebSocket, payload: bytes) -> None:
    _send_sync(loop, websocket.send_bytes(payload))


def _stream_one_text_chunk(
    model: FasterQwen3TTS,
    loop: asyncio.AbstractEventLoop,
    websocket: WebSocket,
    stop_event: asyncio.Event,
    cfg: SessionConfig,
    seq: int,
    text: str,
    prompt_items, 
) -> None:
    t0 = time.perf_counter()
    part_seq = 0
    first_audio_ms: float | None = None

    try:
        if stop_event.is_set():
            return

        _send_json_sync(
            loop,
            websocket,
            {
                "event": "chunk_begin",
                "seq": seq,
                "words": len(text.split()),
                "text": text,
            },
        )

        stream = model.generate_voice_clone_streaming(
            text=text,
            language=cfg.lang,
            voice_clone_prompt=prompt_items,
            xvec_only=True,
            chunk_size=STREAM_CHUNK_SIZE,
        )

        for item in stream:
            if stop_event.is_set():
                break

            if isinstance(item, tuple):
                audio = item[0]
                if len(item) > 1 and isinstance(item[1], (int, float)):
                    _ = int(item[1])
                else:
                    _ = SAMPLE_RATE
            elif isinstance(item, dict):
                audio = item.get("audio") or item.get("wav") or item.get("waveform")
            else:
                audio = item

            if audio is None:
                continue

            pcm = wav_to_pcm16_bytes(audio)
            if first_audio_ms is None:
                first_audio_ms = (time.perf_counter() - t0) * 1000.0

            frame = struct.pack("<III", seq, part_seq, len(pcm)) + pcm
            _send_bytes_sync(loop, websocket, frame)
            part_seq += 1

    except WebSocketClosedError:
        stop_event.set()
        return
    except Exception as e:
        stop_event.set()
        try:
            _send_json_sync(
                loop,
                websocket,
                {
                    "event": "error",
                    "scope": "chunk",
                    "seq": seq,
                    "message": str(e),
                },
            )
        except Exception:
            pass
        raise
    finally:
        elapsed = time.perf_counter() - t0
        if not stop_event.is_set():
            try:
                _send_json_sync(
                    loop,
                    websocket,
                    {
                        "event": "chunk_end",
                        "seq": seq,
                        "parts": part_seq,
                        "elapsed": round(elapsed, 3),
                        "first_audio_ms": round(first_audio_ms, 1) if first_audio_ms is not None else None,
                    },
                )
            except Exception:
                pass


@app.websocket("/api/generate")
async def ws_generate(websocket: WebSocket):
    await websocket.accept()
    loop = asyncio.get_running_loop()
    model = get_model()

    cfg: SessionConfig | None = None
    chunk_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=16)
    stop_event = asyncio.Event()

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

                    ref_audio_b64 = str(msg.get("ref_audio", ""))
                    ref_text = msg.get("ref_text", "")
                    lang = msg.get("lang", "Russian")

                    ref_path, delete_ref = _resolve_ref_audio_path(ref_audio_b64)
                    cfg = SessionConfig(
                        ref_audio_path=ref_path,
                        ref_text=str(ref_text),
                        lang=str(lang),
                        delete_ref=delete_ref,
                    )

                    await websocket.send_json(
                        {
                            "event": "started",
                            "sample_rate": SAMPLE_RATE,
                            "window_size": WINDOW_SIZE,
                            "stream_chunk_size": STREAM_CHUNK_SIZE,
                        }
                    )

                elif event == "chunk":
                    if cfg is None:
                        await websocket.send_json({"event": "error", "message": "send start first"})
                        continue

                    seq = int(msg["seq"])
                    text = str(msg["text"]).strip()
                    if text and not stop_event.is_set():
                        await chunk_queue.put({"seq": seq, "text": text})

                elif event == "end":
                    break

                else:
                    await websocket.send_json({"event": "error", "message": f"unknown event: {event}"})

        except WebSocketDisconnect:
            log.info("Клиент отключился")
            stop_event.set()
        finally:
            if not stop_event.is_set():
                try:
                    await chunk_queue.put(None)
                except Exception:
                    pass

    async def worker() -> None:
        nonlocal cfg
        prompt_items = None
        while True:
            if stop_event.is_set() and chunk_queue.empty():
                break

            try:
                item = await asyncio.wait_for(chunk_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if stop_event.is_set():
                    break
                continue

            if item is None:
                break

            if cfg is None or stop_event.is_set():
                continue
            if not prompt_items:
                prompt_items = model.model.create_voice_clone_prompt(
                    ref_audio=cfg.ref_audio_path,
                    ref_text=cfg.ref_text,
                    x_vector_only_mode=True,
                )
            try:
                await asyncio.to_thread(
                    _stream_one_text_chunk,
                    model,
                    loop,
                    websocket,
                    stop_event,
                    cfg,
                    item["seq"],
                    item["text"],
                    prompt_items
                )
            except WebSocketClosedError:
                stop_event.set()
                break
            except Exception as e:
                stop_event.set()
                try:
                    await websocket.send_json(
                        {
                            "event": "error",
                            "scope": "worker",
                            "message": str(e),
                        }
                    )
                except Exception:
                    pass
                break

        try:
            if not stop_event.is_set():
                await websocket.send_json({"event": "done"})
        except Exception:
            pass

    try:
        await asyncio.gather(receiver(), worker())
    finally:
        stop_event.set()

        if cfg is not None and cfg.delete_ref:
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
        "default_ref": str(settings.default_ref_wav),
    }


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")