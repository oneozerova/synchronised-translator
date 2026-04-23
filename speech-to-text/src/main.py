import asyncio
import json
import time
from functools import partial
from collections import Counter

import numpy as np
import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

from src.VAD_processing import VADProcessor


# ================= CONFIG =================

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

SAMPLE_RATE = 16000
VAD_THRESHOLD = 0.75

SILENCE_TIMEOUT = 0.6
PENDING_WORDS = 3
PROMPT_WORDS = 10

WINDOW_SEC = 1.25
OVERLAP_SEC = 0.0

WINDOW = int(SAMPLE_RATE * WINDOW_SEC)
OVERLAP = int(SAMPLE_RATE * OVERLAP_SEC)

STABILITY_THRESHOLD = 2


# ================= MODELS =================

vad = VADProcessor(device="cuda")

vox_processor = AutoProcessor.from_pretrained(
    "mistralai/Voxtral-Mini-4B-Realtime-2602"
)

vox_model = AutoModelForSpeechSeq2Seq.from_pretrained(
    "mistralai/Voxtral-Mini-4B-Realtime-2602",
    torch_dtype=torch.float16,
).to(DEVICE)

vox_model.eval()


# ================= HELPERS =================

def norm_word(w: str) -> str:
    return w.lower().strip(".,!?…-_\"'")


def build_prompt(words):
    return " ".join(words[-PROMPT_WORDS:])


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


def preprocess_audio(chunk: np.ndarray) -> np.ndarray:
    chunk = chunk - np.mean(chunk)
    max_val = np.max(np.abs(chunk)) + 1e-6
    return (chunk / max_val).astype(np.float32)


# ================= AUDIO BUFFER =================

class AudioRingBuffer:
    def __init__(self, max_samples: int):
        self._buf = np.zeros(max_samples * 2, dtype=np.float32)
        self._max = max_samples
        self._write = 0
        self._size = 0

    def append(self, chunk: np.ndarray):
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
            self._buf[:n - first] = chunk[first:]

        self._write = end % len(self._buf)
        self._size = min(self._size + n, self._max)

    def get(self) -> np.ndarray:
        if self._size < self._max:
            start = (self._write - self._size) % len(self._buf)
        else:
            start = self._write

        if start + self._size <= len(self._buf):
            return self._buf[start:start + self._size].copy()
        else:
            tail = len(self._buf) - start
            return np.concatenate([self._buf[start:], self._buf[:self._size - tail]])

    def trim_to(self, n: int):
        self._size = min(self._size, n)


# ================= TRANSCRIBE =================

def transcribe_voxtral(audio, prompt=""):
    inputs = vox_processor(
        audio,
        sampling_rate=16000,
        return_tensors="pt"
    )

    input_features = inputs.input_features.to(DEVICE)

    with torch.no_grad():
        generated_ids = vox_model.generate(
            input_features=input_features,
            max_new_tokens=128,
            do_sample=False,
        )

    text = vox_processor.batch_decode(
        generated_ids,
        skip_special_tokens=True
    )[0].strip()

    if not text:
        return []

    words = text.split()

    return [(w, i) for i, w in enumerate(words)]  # fake positions


# ================= APP =================

app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()

    # --- init message (optional) ---
    msg = await websocket.receive()

    if "text" in msg:
        init_data = json.loads(msg["text"])
    else:
        init_data = {}

    print("Connected")

    queue: asyncio.Queue = asyncio.Queue()

    # ================= RECEIVER =================

    async def receiver():
        try:
            while True:
                msg = await websocket.receive()

                if "bytes" in msg:
                    await queue.put(msg["bytes"])
                elif "text" in msg:
                    # ignore control messages
                    continue
        except WebSocketDisconnect:
            await queue.put(None)

    # ================= PROCESSOR =================

    async def processor():
        audio_ring = AudioRingBuffer(WINDOW + OVERLAP)

        committed = []
        last_candidates = []

        word_seen = {}
        word_count = {}

        last_voice = time.monotonic()
        loop = asyncio.get_event_loop()

        while True:
            raw = await queue.get()
            if raw is None:
                break

            t0 = time.monotonic()

            chunk = np.frombuffer(raw, dtype=np.float32)
            chunk = preprocess_audio(chunk)

            speech_chunk, speech = await loop.run_in_executor(
                None,
                vad.extract_speech_float32,
                chunk,
                VAD_THRESHOLD,
            )

            pending = []

            if speech:
                audio_ring.append(speech_chunk)
                audio_window = audio_ring.get()

                last_voice = time.monotonic()

                m0 = time.monotonic()

                word_times = await loop.run_in_executor(
                    None,
                    partial(
                        transcribe_voxtral,
                        audio_window,
                        build_prompt(committed),
                    ),
                )

                m1 = time.monotonic()

                if not word_times:
                    await _log(websocket, committed, pending, t0, time.monotonic(), m0, m1, speech)
                    continue

                all_words = [w for w, _ in word_times]

                if is_hallucinating(all_words) or has_ngram_loop(all_words):
                    audio_ring.trim_to(OVERLAP)
                    vad.reset_states()
                    word_seen.clear()
                    word_count.clear()
                    last_candidates = []
                    continue

                for idx, (w, _) in enumerate(word_times):
                    abs_pos = len(committed) + idx
                    w_norm = norm_word(w)

                    if word_seen.get(abs_pos) == w_norm:
                        word_count[abs_pos] = word_count.get(abs_pos, 1) + 1
                    else:
                        word_seen[abs_pos] = w_norm
                        word_count[abs_pos] = 1

                    if word_count[abs_pos] >= STABILITY_THRESHOLD:
                        if abs_pos == len(committed):
                            committed.append(w)
                            word_seen.pop(abs_pos, None)
                            word_count.pop(abs_pos, None)

                pending = [w for w, _ in word_times[len(committed):]]
                last_candidates = all_words

            else:
                if time.monotonic() - last_voice > SILENCE_TIMEOUT:
                    if last_candidates:
                        for w in last_candidates[-PENDING_WORDS:]:
                            if w not in committed:
                                committed.append(w)

                    last_candidates = []
                    audio_ring.trim_to(OVERLAP)
                    vad.reset_states()
                    word_seen.clear()
                    word_count.clear()

                m0 = m1 = None

            await _log(websocket, committed, pending, t0, time.monotonic(), m0, m1, speech)

    await asyncio.gather(receiver(), processor())


# ================= LOG =================

async def _log(ws, committed, pending, t0, t1, m0, m1, speech):
    time_text = f"full time - {t1 - t0:.4f}"

    if speech and m0 and m1:
        time_text += f" | model_time - {m1 - m0:.4f}"

    try:
        await ws.send_text(json.dumps({
            "stable": " ".join(committed),
            "pending": " ".join(pending[-PENDING_WORDS:]),
            "chars": len(" ".join(committed)),
            "speed_logs": time_text,
        }))
    except RuntimeError:
        print("client disconnected")