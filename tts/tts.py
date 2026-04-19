"""
FastAPI Streaming TTS Backend
==============================
Запуск:
    pip install fastapi uvicorn python-multipart faster-qwen3-tts
    uvicorn server:app --host 0.0.0.0 --port 8000

WebSocket эндпоинт: ws://localhost:8000/api/generate
REST эндпоинт (полный текст): POST /api/generate/sync

Протокол WebSocket:
  Клиент отправляет JSON:
    {"ref_audio": "<base64>", "ref_text": "...", "lang": "Russian", "text": "..."}
  Сервер стримит бинарные чанки PCM (int16, 24000 Hz)
  После завершения - JSON {"event": "done", "chunks": N, "elapsed": X}
  При ошибке - JSON {"event": "error", "message": "..."}
"""

from __future__ import annotations

import asyncio
import base64
import io
import struct
import tempfile
import time
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import numpy as np
import torch
import soundfile as sf
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from faster_qwen3_tts import FasterQwen3TTS

# ─── Настройки ────────────────────────────────────────────────────────────────

MODEL_NAME   = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
SAMPLE_RATE  = 24000
WINDOW_SIZE  = 10          # слов в одном окне синтеза
DTYPE        = torch.bfloat16

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tts-server")


# ─── Модель (singleton) ───────────────────────────────────────────────────────

_model: FasterQwen3TTS | None = None


def get_model() -> FasterQwen3TTS:
    global _model
    if _model is None:
        raise RuntimeError("Модель не загружена")
    return _model


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    log.info(f"Загружаем модель {MODEL_NAME}...")
    _model = FasterQwen3TTS.from_pretrained(
        MODEL_NAME,
        device="cuda" if torch.cuda.is_available() else "cpu",
        dtype=DTYPE,
    )
    # Прогрев
    log.info("Прогрев модели...")
    _model.generate_voice_clone(
        text="warmup warmup warmup",
        language="English",
        ref_audio=_get_warmup_audio(),
        ref_text="warmup",
        xvec_only=True,
    )
    log.info("Модель готова ✅")
    yield
    log.info("Завершение работы...")


def _get_warmup_audio() -> str:
    """Создаёт временный тишинный WAV для прогрева."""
    silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, silence, SAMPLE_RATE)
        return f.name


app = FastAPI(title="Streaming TTS API", lifespan=lifespan)


# ─── Утилиты ──────────────────────────────────────────────────────────────────

def wav_to_pcm16(wav_array: np.ndarray) -> bytes:
    """Конвертирует float32 массив в int16 PCM bytes."""
    clipped = np.clip(wav_array, -1.0, 1.0)
    return (clipped * 32767).astype(np.int16).tobytes()


def bytes_to_wav_file(audio_bytes: bytes) -> str:
    """Сохраняет bytes (base64 декодированные) во временный WAV файл."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        return f.name


def synthesize_window(
    model:     FasterQwen3TTS,
    words:     list[str],
    language:  str,
    ref_audio: str,
    ref_text:  str,
) -> np.ndarray | None:
    """Синтезирует одно окно слов, возвращает numpy waveform."""
    text = " ".join(words)
    if not text.strip():
        return None
    try:
        wav, _sr = model.generate_voice_clone(
            text=text,
            language=language,
            ref_audio=ref_audio,
            ref_text=ref_text,
            xvec_only=True,
        )
        return wav[0] if isinstance(wav, list) else wav
    except Exception as e:
        log.error(f"Ошибка синтеза '{text[:40]}': {e}")
        return None


# ─── WebSocket streaming эндпоинт ─────────────────────────────────────────────

@app.websocket("/api/generate")
async def ws_generate(websocket: WebSocket):
    """
    WebSocket streaming TTS.

    Клиент отправляет ONE JSON сообщение:
    {
        "ref_audio": "<base64 encoded WAV bytes>",
        "ref_text":  "транскрипция референсного аудио",
        "lang":      "Russian",
        "text":      "текст для синтеза"
    }

    Сервер отвечает:
      - бинарные сообщения: PCM int16 аудио чанки (24000 Hz, mono)
      - финальное JSON: {"event": "done", "chunks": N, "elapsed": X, "sample_rate": 24000}

    Заголовок каждого бинарного чанка (первые 4 байта):
      uint32 little-endian = длина PCM данных в байтах (для парсинга на клиенте)
    """
    await websocket.accept()
    model = get_model()

    ref_audio_path: str | None = None

    try:
        # Получаем запрос
        data = await websocket.receive_json()
        ref_audio_b64: str = data["ref_audio"]
        ref_text: str      = data.get("ref_text", "")
        lang: str          = data.get("lang", "Russian")
        text: str          = data["text"]

        # Декодируем ref_audio из base64 во временный файл
        audio_bytes   = base64.b64decode(ref_audio_b64)
        ref_audio_path = bytes_to_wav_file(audio_bytes)

        words   = text.split()
        window: list[str] = []
        chunks_sent = 0
        t_start = time.perf_counter()

        log.info(f"WS синтез: lang={lang}, words={len(words)}, ref_text='{ref_text[:40]}'")

        for i, word in enumerate(words):
            window.append(word)
            is_last = (i == len(words) - 1)

            if len(window) >= WINDOW_SIZE or is_last:
                # Синтез в отдельном потоке чтобы не блокировать event loop
                wav = await asyncio.get_event_loop().run_in_executor(
                    None,
                    synthesize_window,
                    model, window, lang, ref_audio_path, ref_text,
                )
                window.clear()

                if wav is not None:
                    pcm = wav_to_pcm16(wav)
                    # Отправляем: 4 байта длина + PCM данные
                    header = struct.pack("<I", len(pcm))
                    await websocket.send_bytes(header + pcm)
                    chunks_sent += 1
                    log.debug(f"  Отправлен чанк {chunks_sent}: {len(pcm)//2} сэмплов")

        elapsed = time.perf_counter() - t_start
        await websocket.send_json({
            "event":       "done",
            "chunks":      chunks_sent,
            "elapsed":     round(elapsed, 3),
            "sample_rate": SAMPLE_RATE,
        })
        log.info(f"WS завершён: {chunks_sent} чанков за {elapsed:.2f}с")

    except WebSocketDisconnect:
        log.info("Клиент отключился")
    except KeyError as e:
        await websocket.send_json({"event": "error", "message": f"Отсутствует поле: {e}"})
    except Exception as e:
        log.exception("Ошибка в WS обработчике")
        try:
            await websocket.send_json({"event": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if ref_audio_path:
            Path(ref_audio_path).unlink(missing_ok=True)
        try:
            await websocket.close()
        except Exception:
            pass


# ─── REST эндпоинт (полный текст → WAV файл) ─────────────────────────────────

@app.post("/api/generate/sync")
async def rest_generate(
    ref_audio: UploadFile = File(...),
    ref_text:  str        = Form(""),
    lang:      str        = Form("Russian"),
    text:      str        = Form(...),
):
    """
    Синхронный REST эндпоинт.
    Принимает multipart/form-data, возвращает WAV файл.
    """
    model = get_model()

    audio_bytes    = await ref_audio.read()
    ref_audio_path = bytes_to_wav_file(audio_bytes)

    try:
        words  = text.split()
        chunks = [words[i:i+WINDOW_SIZE] for i in range(0, len(words), WINDOW_SIZE)]
        all_wav: list[np.ndarray] = []

        t0 = time.perf_counter()
        for window in chunks:
            wav = await asyncio.get_event_loop().run_in_executor(
                None, synthesize_window, model, window, lang, ref_audio_path, ref_text
            )
            if wav is not None:
                all_wav.append(wav)

        if not all_wav:
            raise HTTPException(500, "Синтез не дал результата")

        final = np.concatenate(all_wav)
        elapsed = time.perf_counter() - t0
        log.info(f"REST синтез: {len(words)} слов за {elapsed:.2f}с")

        buf = io.BytesIO()
        sf.write(buf, final, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        buf.seek(0)

        return StreamingResponse(
            buf,
            media_type="audio/wav",
            headers={"X-Elapsed": str(round(elapsed, 3))},
        )
    finally:
        Path(ref_audio_path).unlink(missing_ok=True)


# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model":  MODEL_NAME,
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "loaded": _model is not None,
    }


# ─── Точка входа ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")