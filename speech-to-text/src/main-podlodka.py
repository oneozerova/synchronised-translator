import asyncio
import json
import time
from collections import Counter

import numpy as np
import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from transformers import pipeline

from src.VAD_processing import VADProcessor


# ─────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────
VAD_THRESHOLD = 0.75

SAMPLE_RATE = 16000
WINDOW_SEC = 1.5
OVERLAP_SEC = 0.25

WINDOW = int(SAMPLE_RATE * WINDOW_SEC)
OVERLAP = int(SAMPLE_RATE * OVERLAP_SEC)

COMMIT_WORDS = 5
PROMPT_WORDS = 20
SILENCE_TIMEOUT = 0.6

DEVICE = 0 if torch.cuda.is_available() else -1

asr = pipeline(
    "automatic-speech-recognition",
    model="bond005/whisper-podlodka-turbo",
    device=DEVICE
)

vad = VADProcessor(device="cuda")
app = FastAPI()


# ─────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────
def norm_word(w: str) -> str:
    return w.lower().strip(".,!?…-_\"'()[]{}:;")

def find_anchor(committed, all_words):
    norm_all = [norm_word(w) for w in all_words]
    norm_comm = [norm_word(w) for w in committed]

    for tail_len in range(min(5, len(norm_comm)), 0, -1):
        tail = norm_comm[-tail_len:]
        for i in range(len(norm_all) - tail_len + 1):
            if norm_all[i:i + tail_len] == tail:
                return i + tail_len
    return 0

def is_hallucinating(words, threshold=0.6, min_len=6):
    if len(words) < min_len:
        return False
    counts = Counter(words)
    return counts.most_common(1)[0][1] / len(words) > threshold

def has_ngram_loop(words, n=3):
    if len(words) < n * 2:
        return False
    ngrams = [tuple(words[i:i+n]) for i in range(len(words) - n + 1)]
    return Counter(ngrams).most_common(1)[0][1] > 2

def build_prompt(words):
    return " ".join(words[-PROMPT_WORDS:])

def preprocess_audio(chunk):
    chunk = chunk - np.mean(chunk)
    max_val = np.max(np.abs(chunk)) + 1e-6
    return (chunk / max_val).astype(np.float32)


# ─────────────────────────────────────────────────────────
# AUDIO BUFFER
# ─────────────────────────────────────────────────────────
class AudioRingBuffer:
    def __init__(self, max_samples: int):
        self._buf = np.zeros(max_samples * 2, dtype=np.float32)
        self._max = max_samples
        self._write = 0
        self._size = 0

    def append(self, chunk):
        n = len(chunk)
        if n >= self._max:
            self._buf[:self._max] = chunk[-self._max:]
            self._write = self._max
            self._size = self._max
            return

        end = self._write + n
        if end <= len(self._buf):
            self._buf[self._write:end] = chunk
        else:
            first = len(self._buf) - self._write
            self._buf[self._write:] = chunk[:first]
            self._buf[:n-first] = chunk[first:]

        self._write = end % len(self._buf)
        self._size = min(self._size + n, self._max)

    def get(self):
        if self._size == 0:
            return np.empty(0, dtype=np.float32)

        if self._size < self._max:
            start = (self._write - self._size) % len(self._buf)
        else:
            start = self._write

        if start + self._size <= len(self._buf):
            return self._buf[start:start+self._size].copy()

        tail = len(self._buf) - start
        return np.concatenate([self._buf[start:], self._buf[:self._size-tail]])

    def trim_to(self, n):
        self._size = min(self._size, n)


# ─────────────────────────────────────────────────────────
# TRANSLATION (HF)
# ─────────────────────────────────────────────────────────
def transcribe_translate(audio):
    result = asr(
        audio,
        generate_kwargs={
            "task": "translate",
            "language": "russian"
        }
    )

    text = result["text"].strip()
    words = text.split()

    # fake timestamps (для совместимости логики)
    return [(w, i * 0.2) for i, w in enumerate(words)]


# ─────────────────────────────────────────────────────────
# WS
# ─────────────────────────────────────────────────────────
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
        loop = asyncio.get_running_loop()

        audio_ring = AudioRingBuffer(WINDOW + OVERLAP)
        committed = []
        pending = []

        last_voice = time.monotonic()

        while True:
            raw = await queue.get()
            if raw is None:
                break

            t0 = time.monotonic()

            chunk = np.frombuffer(raw, dtype=np.float32)
            chunk = preprocess_audio(chunk)

            speech_chunk, speech = await loop.run_in_executor(
                None, vad.extract_speech_float32, chunk, VAD_THRESHOLD
            )

            if speech:
                audio_ring.append(speech_chunk)
                audio_window = audio_ring.get()
                last_voice = time.monotonic()

                word_times = await loop.run_in_executor(
                    None,
                    transcribe_translate,
                    audio_window
                )

                if word_times:
                    all_words = [w for w, _ in word_times]

                    if is_hallucinating(all_words) or has_ngram_loop(all_words):
                        audio_ring.trim_to(OVERLAP)
                        vad.reset_states()
                        pending.clear()
                        continue

                    anchor = find_anchor(committed + pending, all_words)
                    new_words = all_words[anchor:]

                    pending.extend(new_words)

                    while len(pending) >= COMMIT_WORDS:
                        committed.extend(pending[:COMMIT_WORDS])
                        pending = pending[COMMIT_WORDS:]

            else:
                if time.monotonic() - last_voice > SILENCE_TIMEOUT:
                    committed.extend(pending)
                    pending.clear()
                    audio_ring.trim_to(OVERLAP)
                    vad.reset_states()

            t1 = time.monotonic()

            await websocket.send_text(json.dumps({
                "stable": " ".join(committed),
                "pending": " ".join(pending[:COMMIT_WORDS]),
                "time": t1 - t0
            }))

    await asyncio.gather(receiver(), processor())