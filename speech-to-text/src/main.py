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
model = WhisperModel("small", device="cuda", compute_type="int8_float16")

vad = VADProcessor(device="cuda")


from collections import Counter

def norm_word(w: str) -> str:
    return w.lower().strip(".,!?…-_\"'")

def find_anchor(committed: list[str], all_words: list[str]) -> int:
    norm_all = [norm_word(w) for w in all_words]
    norm_comm = [norm_word(w) for w in committed]
    for tail_len in range(min(5, len(norm_comm)), 0, -1):
        tail = norm_comm[-tail_len:]
        for i in range(len(norm_all) - tail_len + 1):
            if norm_all[i:i + tail_len] == tail:
                return i + tail_len
    return 0

def is_hallucinating(words: list[str], threshold: float = 0.6, min_len: int = 6) -> bool:
    if len(words) < min_len:
        return False
    counts = Counter(words)
    return counts.most_common(1)[0][1] / len(words) > threshold

def has_ngram_loop(words: list[str], n: int = 3) -> bool:
    if len(words) < n * 2:
        return False
    ngrams = [tuple(words[i:i+n]) for i in range(len(words) - n + 1)]
    return Counter(ngrams).most_common(1)[0][1] > 2


def transcribe(audio, prompt=""):
    segments, _ = model.transcribe(
        audio,
        task="translate",
        language="ru",
        vad_filter=True,
        initial_prompt=prompt or None,
        beam_size=1,
        best_of=1,
        temperature=[0.0, 0.2, 0.4],   # fallback temperatures — ключевой фикс!
        condition_on_previous_text=False, # True провоцирует loops при плохом промпте
        compression_ratio_threshold=1.35, # снизить — агрессивнее отсекает repetitions
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        repetition_penalty=1.3,          # есть в faster-whisper >= 1.0
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

        # ───── НОВОЕ: буфер подтверждения ─────
        STABILITY_THRESHOLD = 2      # слово должно появиться N раз подряд
        word_seen: dict[int, str] = {}    # позиция → последнее слово на этой позиции
        word_count: dict[int, int] = {}   # позиция → сколько раз подряд оно там стояло
        # ──────────────────────────────────────

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

                # ───── НОВОЕ: проверка галлюцинаций ─────
                all_words = [w for w, _ in word_times]
                if is_hallucinating(all_words) or has_ngram_loop(all_words):
                    print("⚠️ Hallucination detected, resetting window")
                    audio_window = audio_window[-OVERLAP:]
                    vad.reset_states()
                    word_seen.clear()
                    word_count.clear()
                    last_candidates = []
                    continue
                # ─────────────────────────────────────────

                audio_duration = len(audio_window) / SAMPLE_RATE

                # anchor-поиск — заменяем на нормализованный (из прошлого ответа)
                anchor_pos = find_anchor(committed, all_words) if committed else 0

                # ───── НОВОЕ: стабилизация вместо прямого коммита ─────
                for idx, (w, end_t) in enumerate(word_times[anchor_pos:]):
                    abs_pos = len(committed) + idx   # абсолютная позиция в финальном тексте

                    w_norm = norm_word(w)

                    if word_seen.get(abs_pos) == w_norm:
                        word_count[abs_pos] = word_count.get(abs_pos, 1) + 1
                    else:
                        # слово на этой позиции изменилось — сбрасываем счётчик
                        word_seen[abs_pos] = w_norm
                        word_count[abs_pos] = 1

                    stable_enough = word_count[abs_pos] >= STABILITY_THRESHOLD
                    far_from_edge = audio_duration - end_t > SAFE_MARGIN

                    if stable_enough and far_from_edge:
                        # коммитим только последовательно — без пропусков
                        if abs_pos == len(committed):
                            committed.append(w)
                            # чистим буфер для этой позиции — она закрыта
                            word_seen.pop(abs_pos, None)
                            word_count.pop(abs_pos, None)
                # ──────────────────────────────────────────────────────

                # pending — слова у края окна, ещё не стабилизированы
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
                    vad.reset_states()

                    # ───── НОВОЕ: сброс буфера при тишине ─────
                    word_seen.clear()
                    word_count.clear()
                    # ──────────────────────────────────────────

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
