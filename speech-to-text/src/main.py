import asyncio
import json
import time
from functools import partial

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from faster_whisper import WhisperModel
from src.VAD_processing import VADProcessor

VAD_THRESHOLD = 0.75

# model = WhisperModel("small", device="cuda", compute_type="float16")
model = WhisperModel("small", device="cuda", compute_type="float16")

vad = VADProcessor(device="cuda")


def transcribe(audio, prompt=""):
    segments, _ = model.transcribe(
        audio,
        task="translate",          # <-- ключевое изменение
        language="ru",              # автоопределение языка
        vad_filter=True,           # у тебя уже есть свой VAD
        initial_prompt=prompt or None,

        # decoding под streaming
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=True,
        compression_ratio_threshold=2.4,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,

        word_timestamps=True,
    )

    words = []
    for seg in segments:
        for w in seg.words:
            token = w.word.strip()
            if token:
                words.append((token, w.end))

    return words


SAMPLE_RATE = 16000

SILENCE_TIMEOUT = 0.6
PENDING_WORDS = 3
PROMPT_WORDS = 10
WINDOW_SEC = 2.0
OVERLAP_SEC = 0.01

WINDOW = int(SAMPLE_RATE * WINDOW_SEC)
OVERLAP = int(SAMPLE_RATE * OVERLAP_SEC)


def norm(text):
    return [w.lower().strip(".,!?…-_") for w in text.split() if w]


def build_prompt(words):
    return " ".join(words[-PROMPT_WORDS:])


app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok"}


def preprocess_audio(chunk: np.ndarray) -> np.ndarray:
    # remove DC offset
    chunk = chunk - np.mean(chunk)

    # normalize (RMS-like)
    max_val = np.max(np.abs(chunk)) + 1e-6
    chunk = chunk / max_val

    return chunk.astype(np.float32)


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    queue = asyncio.Queue()

    async def receiver():
        try:
            while True:
                raw = await websocket.receive_bytes()
                await queue.put(raw)
        except WebSocketDisconnect:
            await queue.put(None)

    async def processor():
        audio_window = np.array([], dtype=np.float32)
        committed = []
        last_candidates = []
        SAFE_MARGIN = 0.7
        last_voice = time.time()
        loop = asyncio.get_event_loop()

        while True:
            full_time_start = time.time()
            pending = []

            raw = await queue.get()
            if raw is None:
                break

            chunk = np.frombuffer(raw, dtype=np.float32)
            now = time.time()

            chunk = preprocess_audio(chunk=chunk)

            speech_chunk, speech = vad.extract_speech_float32(chunk, VAD_THRESHOLD)
            if speech:
                audio_window = np.concatenate([audio_window, speech_chunk])

                if len(audio_window) > WINDOW:
                    audio_window = audio_window[-(WINDOW + OVERLAP):]

            if speech:
                last_voice = now
                model_processing_start = time.time()
                word_times = await loop.run_in_executor(
                    None, partial(transcribe, audio_window, build_prompt(committed))
                )
                model_processing_end = time.time()

                if not word_times:
                    continue

                audio_duration = len(audio_window) / SAMPLE_RATE
                all_words = [w for w, _ in word_times]

                anchor_pos = 0
                if committed:
                    for tail_len in range(min(4, len(committed)), 0, -1):
                        tail = committed[-tail_len:]
                        for i in range(len(all_words) - tail_len + 1):
                            if all_words[i:i + tail_len] == tail:
                                anchor_pos = i + tail_len
                                break
                        if anchor_pos:
                            break

                for w, end_t in word_times[anchor_pos:]:
                    if audio_duration - end_t > SAFE_MARGIN:
                        committed.append(w)

                pending = [w for w, t in word_times if audio_duration - t <= SAFE_MARGIN]
                last_candidates = all_words
            else:
                print(f"SKIP (VAD)")
                if now - last_voice > SILENCE_TIMEOUT:
                    if last_candidates:
                        for w in last_candidates[-PENDING_WORDS:]:
                            if w not in committed:
                                committed.append(w)
                    last_candidates = []
                    audio_window = audio_window[-OVERLAP:]
                    # [5] Сброс состояния Silero при сбросе окна
                    vad.reset_states()

            full_time_end = time.time()
            time_text = f"full time - {full_time_end - full_time_start}"
            if speech:
                time_text += f" | model_time - {model_processing_end - model_processing_start}"

            print(time_text)
            try:
                await websocket.send_text(json.dumps({
                    "stable": " ".join(committed),
                    "pending": " ".join(pending[-PENDING_WORDS:]),
                    "chars": len(" ".join(committed)),
                    "speed_logs": time_text
                }))
            except RuntimeError:
                print("STOP")

    await asyncio.gather(receiver(), processor())
