import asyncio
import json
import time
from collections import deque
from functools import partial

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from faster_whisper import WhisperModel
from src.VAD_processing import VADProcessor

VAD_THRESHOLD = 0.75

model = WhisperModel("small", device="cuda", compute_type="int8_float16")
vad = VADProcessor(device="cuda")

from collections import Counter

def norm_word(w: str) -> str:
    return w.lower().strip(".,!?…-_\"'")

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

def transcribe(audio, prompt=""):
    segments, _ = model.transcribe(
        audio,
        task="transcribe",
        language="en",
        vad_filter=True,
        initial_prompt=prompt or None,
        beam_size=1,
        best_of=1,
        temperature=[0.0, 0.2, 0.4],
        condition_on_previous_text=False,
        compression_ratio_threshold=1.35,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        repetition_penalty=1.3,
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
WINDOW_SEC = 1.25
OVERLAP_SEC = 0.0

WINDOW = int(SAMPLE_RATE * WINDOW_SEC)
OVERLAP = int(SAMPLE_RATE * OVERLAP_SEC)

def norm(text):
    return [w.lower().strip(".,!?…-_") for w in text.split() if w]

def build_prompt(words):
    return " ".join(words[-PROMPT_WORDS:])


# ─── Ring-buffer вместо concatenate+slice ────────────────────────────────────
class AudioRingBuffer:
    """Хранит последние max_samples сэмплов без лишних копий."""
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
        """Оставить последние n сэмплов."""
        self._size = min(self._size, n)

    def __len__(self):
        return self._size
# ─────────────────────────────────────────────────────────────────────────────


def preprocess_audio(chunk: np.ndarray) -> np.ndarray:
    chunk = chunk - np.mean(chunk)
    max_val = np.max(np.abs(chunk)) + 1e-6
    return (chunk / max_val).astype(np.float32)


app = FastAPI()

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue()

    async def receiver():
        try:
            while True:
                raw = await websocket.receive_bytes()
                await queue.put(raw)
        except WebSocketDisconnect:
            await queue.put(None)

    async def processor():
        # ── FIX 3: ring-buffer вместо concat+slice ──
        audio_ring = AudioRingBuffer(WINDOW + OVERLAP)
        committed = []
        last_candidates = []
        SAFE_MARGIN = 0.7
        last_voice = time.monotonic()   # FIX: monotonic точнее для интервалов
        loop = asyncio.get_event_loop()

        STABILITY_THRESHOLD = 2
        word_seen: dict[int, str] = {}
        word_count: dict[int, int] = {}

        while True:
            # ── FIX 1: измеряем время ПОСЛЕ получения чанка ──────────────────
            raw = await queue.get()
            if raw is None:
                break
            full_time_start = time.monotonic()
            # ─────────────────────────────────────────────────────────────────

            chunk = np.frombuffer(raw, dtype=np.float32)
            now = time.monotonic()

            chunk = preprocess_audio(chunk)

            # ── FIX 2: VAD в executor, чтобы не блокировать event loop ───────
            speech_chunk, speech = await loop.run_in_executor(
                None, vad.extract_speech_float32, chunk, VAD_THRESHOLD
            )
            # ─────────────────────────────────────────────────────────────────

            pending = []

            if speech:
                # FIX 3: ring-buffer
                audio_ring.append(speech_chunk)
                audio_window = audio_ring.get()

                last_voice = now
                model_processing_start = time.monotonic()
                word_times = await loop.run_in_executor(
                    None, partial(transcribe, audio_window, build_prompt(committed))
                )
                model_processing_end = time.monotonic()

                if not word_times:
                    full_time_end = time.monotonic()
                    _log(websocket, committed, pending, full_time_start,
                         full_time_end, None, None, speech)
                    continue

                all_words = [w for w, _ in word_times]
                if is_hallucinating(all_words) or has_ngram_loop(all_words):
                    print("⚠️ Hallucination detected, resetting window")
                    audio_ring.trim_to(OVERLAP)
                    vad.reset_states()
                    word_seen.clear()
                    word_count.clear()
                    last_candidates = []
                    full_time_end = time.monotonic()
                    _log(websocket, committed, pending, full_time_start,
                         full_time_end, model_processing_start, model_processing_end, speech)
                    continue

                audio_duration = len(audio_window) / SAMPLE_RATE
                anchor_pos = find_anchor(committed, all_words) if committed else 0

                for idx, (w, end_t) in enumerate(word_times[anchor_pos:]):
                    abs_pos = len(committed) + idx
                    w_norm = norm_word(w)

                    if word_seen.get(abs_pos) == w_norm:
                        word_count[abs_pos] = word_count.get(abs_pos, 1) + 1
                    else:
                        word_seen[abs_pos] = w_norm
                        word_count[abs_pos] = 1

                    stable_enough = word_count[abs_pos] >= STABILITY_THRESHOLD
                    far_from_edge = audio_duration - end_t > SAFE_MARGIN

                    if stable_enough and far_from_edge:
                        if abs_pos == len(committed):
                            committed.append(w)
                            word_seen.pop(abs_pos, None)
                            word_count.pop(abs_pos, None)

                pending = [w for w, t in word_times
                           if audio_duration - t <= SAFE_MARGIN]
                last_candidates = all_words

            else:
                print("SKIP (VAD)")
                if now - last_voice > SILENCE_TIMEOUT:
                    if last_candidates:
                        for w in last_candidates[-PENDING_WORDS:]:
                            if w not in committed:
                                committed.append(w)
                    last_candidates = []
                    audio_ring.trim_to(OVERLAP)
                    vad.reset_states()
                    word_seen.clear()
                    word_count.clear()
                model_processing_start = model_processing_end = None

            full_time_end = time.monotonic()
            await _log(websocket, committed, pending,
                       full_time_start, full_time_end,
                       model_processing_start if speech else None,
                       model_processing_end if speech else None,
                       speech)

    await asyncio.gather(receiver(), processor())


async def _log(ws, committed, pending, t0, t1, m0, m1, speech):
    time_text = f"full time - {t1 - t0:.4f}"
    if speech and m0 and m1:
        time_text += f" | model_time - {m1 - m0:.4f}"
    print(time_text)
    try:
        await ws.send_text(json.dumps({
            "stable": " ".join(committed),
            "pending": " ".join(pending[-PENDING_WORDS:]),
            "chars": len(" ".join(committed)),
            "speed_logs": time_text,
        }))
    except RuntimeError:
        print("STOP")