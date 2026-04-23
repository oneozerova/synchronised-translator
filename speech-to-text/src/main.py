"""
Streaming Russian STT + English translation via WebSocket
Algorithm: LocalAgreement-2 (Macháček et al., 2023 / whisper_streaming)

Ключевые исправления по сравнению с предыдущей версией:
  1. Модель получает ТОЛЬКО окно [committed_end−OVERLAP .. +MAX_WINDOW],
     а НЕ полный audio_buffer. Это:
     - фиксирует повторный коммит одних и тех же слов
       (Whisper смещает timestamps при изменении длины входа)
     - ограничивает время модели: ~800ms на 7s аудио вместо O(n)
  2. hyp_buf.insert() получает window_start_sec как offset,
     а не buffer_time_offset — только так absolute timestamps корректны
  3. new_samples считается по абсолютному времени, а не индексам

Запуск: uvicorn main:app --host 0.0.0.0 --port 8001
"""

import asyncio
import json
import time
from collections import Counter
from functools import partial

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from faster_whisper import WhisperModel
from src.VAD_processing import VADProcessor

# ══════════════════════════════════════════════════════════════════════════════
#  КОНФИГУРАЦИЯ
# ══════════════════════════════════════════════════════════════════════════════

TASK     = "translate"   # "transcribe" → рус | "translate" → eng
LANGUAGE = "ru"

VAD_THRESHOLD = 0.75

# ──────────────────────────────────────────────────────────────────────────────
#  МОДЕЛИ
# ──────────────────────────────────────────────────────────────────────────────

# [АКТИВНА] large-v3 поддерживает task="translate"
model = WhisperModel("large-v3", device="cuda", compute_type="int8_float16")

# [ПОДЛОДКА LARGE] только TASK="transcribe"
# model = WhisperModel("avazir/faster-distil-whisper-large-v3-ru", device="cuda", compute_type="float16")
# TASK = "transcribe"

# [ПОДЛОДКА TURBO] только TASK="transcribe", быстрее ~4x
# model = WhisperModel("koekaverna/faster-whisper-podlodka-turbo", device="cuda", compute_type="float16")
# TASK = "transcribe"

# [CPU FALLBACK]
# model = WhisperModel("medium", device="cpu", compute_type="int8")
# TASK = "translate"

vad = VADProcessor(device="cuda")

SAMPLE_RATE = 16000

# ──────────────────────────────────────────────────────────────────────────────
#  ПАРАМЕТРЫ
# ──────────────────────────────────────────────────────────────────────────────

# Минимум нового аудио после last_committed_time чтобы запустить модель.
MIN_NEW_SEC = 1.0

# Аудио ДО committed_end включаемое в окно для контекста Whisper.
# 1.5s даёт хороший акустический контекст для translate.
OVERLAP_SEC = 1.5

# Максимальный размер окна подаваемого в модель.
# large-v3: ~100ms inference/sec аудио → 7s ≈ 700ms. Ограничивает задержку.
MAX_WINDOW_SEC = 8.0

# Тримить speech_buf когда он длиннее этого (только для памяти, не влияет на модель).
BUFFER_TRIM_SEC = 10.0

# Prompt: последние N закоммиченных слов
PROMPT_WORDS = 10

# beam_size=5 (как в оригинальном whisper_streaming) даёт более стабильные
# гипотезы между итерациями — LocalAgreement сходится быстрее.
BEAM_SIZE = 5

GRACE_SEC = 0.4

MIN_NEW_SAMPLES    = int(SAMPLE_RATE * MIN_NEW_SEC)
OVERLAP_SAMPLES    = int(SAMPLE_RATE * OVERLAP_SEC)
MAX_WINDOW_SAMPLES = int(SAMPLE_RATE * MAX_WINDOW_SEC)
BUFFER_TRIM_SAMP   = int(SAMPLE_RATE * BUFFER_TRIM_SEC)


# ══════════════════════════════════════════════════════════════════════════════
#  HYPOTHESIS BUFFER  (LocalAgreement-2)
#  Портировано из whisper_streaming/whisper_online.py
#  Macháček, Dabre, Bojar — IJCNLP-AACL 2023
# ══════════════════════════════════════════════════════════════════════════════

import re
import unicodedata

def norm_word(w: str) -> str:
    w = unicodedata.normalize("NFKC", w).strip().lower()
    w = re.sub(r"[^\w\u0400-\u04FF]+$", "", w)  # убрать хвостовую пунктуацию
    return w

class HypothesisBuffer:
    """
    LocalAgreement-2: коммитит слова подтверждённые двумя последовательными
    транскрипциями.

    insert(words, offset) → подготовить текущую гипотезу (с n-gram cleanup)
    flush()              → закоммитить общий префикс prev/curr гипотез
    complete()           → текущие незакоммиченные слова (для pending)
    """

    def __init__(self):
        # Закоммиченные слова ещё в диапазоне буфера (для n-gram lookup)
        self.committed_in_buffer: list[tuple[float, float, str]] = []
        self.buffer:              list[tuple[float, float, str]] = []  # prev hypothesis
        self.new:                 list[tuple[float, float, str]] = []  # curr hypothesis
        self.last_committed_time: float = 0.0
        self.last_committed_word: str   = ""

    def insert(self, new_words: list[tuple[float, float, str]], offset: float) -> None:
        """
        new_words : [(start_rel, end_rel, text), ...] — время относительно
                    начала audio_window переданного в transcribe()
        offset    : абсолютное время начала того audio_window (секунды)

        Шаги:
        1. Перевести в абсолютные времена
        2. Отбросить слова до last_committed_time (-0.1s допуск)
        3. N-gram overlap removal: убрать слова из overlap-контекста,
           которые Whisper повторил из уже-закоммиченного аудио
        """
        # 1. Абсолютные времена
        new_abs = [(a + offset, b + offset, t) for a, b, t in new_words]

        # 2. Фильтр по времени
        self.new = [
            (a, b, t) for a, b, t in new_abs
            # if a > self.last_committed_time - 0.1  # или 0.6
            if b > self.last_committed_time - GRACE_SEC
        ]
        if not self.new:
            return

        # 3. N-gram overlap removal
        # Если первое слово near last_committed_time → Whisper мог повторить
        # конец уже-закоммиченного аудио из OVERLAP. Ищем совпадение хвоста
        # committed_in_buffer с началом new (1-5 слов).
        a0, _, _ = self.new[0]
        if abs(a0 - self.last_committed_time) < 1.0 and self.committed_in_buffer:
            cn = len(self.committed_in_buffer)
            nn = len(self.new)
            for i in range(1, min(cn, nn, 5) + 1):
                # Хвост committed: последние i слов в хронологическом порядке
                c_ngram = " ".join(
                    self.committed_in_buffer[-j][2] for j in range(i, 0, -1)
                )
                # Голова new: первые i слов
                n_ngram = " ".join(self.new[k][2] for k in range(i))
                if c_ngram == n_ngram:
                    self.new = self.new[i:]
                    break

    def flush(self):
        commit = []
        i = 0
        while i < min(len(self.buffer), len(self.new)):
            if norm_word(self.buffer[i][2]) != norm_word(self.new[i][2]):
                break
            commit.append(self.new[i])
            i += 1
    
        self.buffer = self.new[i:]
        self.new = []
        self.committed_in_buffer.extend(commit)
    
        if commit:
            self.last_committed_time = commit[-1][1]
            self.last_committed_word = commit[-1][2]
    
        return commit
    
    def pop_committed(self, trim_time: float) -> None:
        """Удалить из committed_in_buffer слова до trim_time (после trim буфера)."""
        while (self.committed_in_buffer
               and self.committed_in_buffer[0][1] <= trim_time):
            self.committed_in_buffer.pop(0)

    def complete(self) -> list[tuple[float, float, str]]:
        """Незакоммиченные слова текущей гипотезы (pending для UI)."""
        return self.buffer

    def reset_hypothesis(self) -> None:
        """Сбросить только незакоммиченную часть (при детекции галлюцинации)."""
        self.buffer = []
        self.new    = []


# ══════════════════════════════════════════════════════════════════════════════
#  ТРАНСКРИПЦИЯ
# ══════════════════════════════════════════════════════════════════════════════

def transcribe(audio: np.ndarray, prompt: str = "") -> list[tuple[float, float, str]]:
    """
    Возвращает [(start_sec, end_sec, word), ...] — время относительно
    начала переданного audio (не абсолютное).

    condition_on_previous_text=False: каждая итерация независима.
    Это критично для LocalAgreement — нужна воспроизводимость результата
    из аудио, а не "сочинение" на базе предыдущего вывода.
    Контекст передаётся через initial_prompt.
    """
    segments, _ = model.transcribe(
        audio,
        task=TASK,
        language=LANGUAGE,
        vad_filter=True,
        initial_prompt=prompt or None,
        beam_size=BEAM_SIZE,
        best_of=1,
        temperature=[0.0],
        condition_on_previous_text=False,
        compression_ratio_threshold=2.4,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        repetition_penalty=1.3,
        word_timestamps=True,
        without_timestamps=False,
    )
    words: list[tuple[float, float, str]] = []
    for seg in segments:
        if seg.words is None:
            continue
        for w in seg.words:
            token = w.word.strip()
            if token:
                words.append((w.start, w.end, token))
    return words


# ══════════════════════════════════════════════════════════════════════════════
#  ГАЛЛЮЦИНАЦИИ
# ══════════════════════════════════════════════════════════════════════════════

def is_hallucinating(words: list[str], threshold: float = 0.5, min_len: int = 6) -> bool:
    if len(words) < min_len:
        return False
    return Counter(words).most_common(1)[0][1] / len(words) > threshold


def has_ngram_loop(words: list[str], n: int = 3) -> bool:
    if len(words) < n * 2:
        return False
    ngrams = [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]
    return Counter(ngrams).most_common(1)[0][1] > 2


# ══════════════════════════════════════════════════════════════════════════════
#  ПРЕПРОЦЕССИНГ
# ══════════════════════════════════════════════════════════════════════════════

def preprocess_audio(chunk: np.ndarray) -> np.ndarray:
    chunk  = chunk - np.mean(chunk)
    maxval = np.max(np.abs(chunk)) + 1e-6
    return (chunk / maxval).astype(np.float32)


# ══════════════════════════════════════════════════════════════════════════════
#  FastAPI
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok", "task": TASK, "language": LANGUAGE}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    raw_queue: asyncio.Queue = asyncio.Queue()

    # ── Receiver: никогда не ждёт модели ─────────────────────────────────────
    async def receiver():
        try:
            while True:
                raw = await websocket.receive_bytes()
                await raw_queue.put(raw)
        except WebSocketDisconnect:
            await raw_queue.put(None)

    # ── Processor ────────────────────────────────────────────────────────────
    async def processor():
        """
        speech_buf        — накопленное речевое аудио (VAD-filtered)
        buffer_time_offset — абс. время начала speech_buf[0] (сек)
                             обновляется только при trim буфера

        Что подаётся в модель:
            window = speech_buf[window_start_rel : window_start_rel + MAX_WINDOW]
            где window_start = max(buf_start, committed_end − OVERLAP)

        Это означает: модель НИКОГДА не видит уже-закоммиченный материал
        дальше OVERLAP_SEC назад → Whisper не может вернуть те же слова
        со смещёнными timestamps → повторный коммит невозможен.
        """
        speech_buf: np.ndarray = np.array([], dtype=np.float32)
        buffer_time_offset = 0.0  # абс. время speech_buf[0]

        hyp_buf = HypothesisBuffer()
        committed_words: list[str] = []

        loop = asyncio.get_event_loop()

        n_calls          = 0
        total_model_time = 0.0
        n_hallucinations = 0
        disconnected     = False

        while not disconnected:

            # ── 1. Дрейнить очередь ──────────────────────────────────────────
            new_chunks: list[np.ndarray] = []

            try:
                raw = await asyncio.wait_for(raw_queue.get(), timeout=0.05)
                if raw is None:
                    disconnected = True
                    break
                new_chunks.append(np.frombuffer(raw, dtype=np.float32))
            except asyncio.TimeoutError:
                pass

            while True:
                try:
                    raw = raw_queue.get_nowait()
                    if raw is None:
                        disconnected = True
                        break
                    new_chunks.append(np.frombuffer(raw, dtype=np.float32))
                except asyncio.QueueEmpty:
                    break

            # ── 2. VAD → speech_buf ───────────────────────────────────────────
            for chunk in new_chunks:
                chunk = preprocess_audio(chunk)
                speech_chunk, is_speech = await loop.run_in_executor(
                    None, vad.extract_speech_float32, chunk, VAD_THRESHOLD
                )
                if is_speech:
                    speech_buf = np.concatenate([speech_buf, speech_chunk])

            # ── 3. Достаточно нового аудио? ───────────────────────────────────
            # "Новое" = аудио после last_committed_time в абс. временной шкале
            buf_end_sec   = buffer_time_offset + len(speech_buf) / SAMPLE_RATE
            new_audio_sec = buf_end_sec - hyp_buf.last_committed_time
            if new_audio_sec < MIN_NEW_SEC:
                continue

            # ── 4. Извлечь окно для модели ────────────────────────────────────
            #
            # window_start = committed_end − OVERLAP_SEC (в абс. времени)
            # Нижняя граница: начало speech_buf (не можем уйти раньше)
            #
            # ПОЧЕМУ это ключевое исправление:
            #   Предыдущая версия передавала весь speech_buf. При росте буфера
            #   с 1.9s → 3.4s Whisper возвращал те же слова с другими timestamps
            #   → они проходили timestamp-фильтр → повторный коммит.
            #   Теперь окно всегда начинается near committed_end: модель не видит
            #   уже-закоммиченный контент (за исключением OVERLAP для контекста).

            committed_rel = max(
                0, int((hyp_buf.last_committed_time - buffer_time_offset) * SAMPLE_RATE)
            )
            window_start_rel = max(0, committed_rel - OVERLAP_SAMPLES)
            audio_window     = speech_buf[window_start_rel:]

            # Ограничить MAX_WINDOW_SEC (капуем с конца — берём свежайшее аудио)
            if len(audio_window) > MAX_WINDOW_SAMPLES:
                audio_window     = audio_window[-MAX_WINDOW_SAMPLES:]
                window_start_rel = len(speech_buf) - MAX_WINDOW_SAMPLES

            # Абсолютное время начала окна → offset для insert()
            window_start_sec = buffer_time_offset + window_start_rel / SAMPLE_RATE
            window_dur       = len(audio_window) / SAMPLE_RATE

            # ── 5. Транскрипция ───────────────────────────────────────────────
            prompt = " ".join(committed_words[-PROMPT_WORDS:])
            t0 = time.monotonic()
            raw_words: list[tuple[float, float, str]] = await loop.run_in_executor(
                None, partial(transcribe, audio_window, prompt)
            )
            model_dt = time.monotonic() - t0
            n_calls         += 1
            total_model_time += model_dt
            avg_ms           = total_model_time / n_calls * 1000

            words_text = [w for _, _, w in raw_words]
            print(
                f"  [{n_calls}] model={model_dt*1000:.0f}ms avg={avg_ms:.0f}ms "
                f"win={window_start_sec:.1f}s+{window_dur:.1f}s "
                f"new={new_audio_sec:.1f}s words={len(raw_words)} "
                f"queue={raw_queue.qsize()}"
            )

            # ── 6. Галлюцинации ───────────────────────────────────────────────
            if is_hallucinating(words_text) or has_ngram_loop(words_text):
                n_hallucinations += 1
                print(f"  ⚠️  HAL #{n_hallucinations}: {words_text[:10]}")
                hyp_buf.reset_hypothesis()
                await _send(websocket, committed_words, [],
                            model_dt, avg_ms, n_calls, n_hallucinations)
                continue

            # ── 7. LocalAgreement ─────────────────────────────────────────────
            # insert() принимает offset = window_start_sec (НЕ buffer_time_offset!)
            # Это критично: timestamps из модели relative to audio_window,
            # offset переводит их в абсолютные.
            hyp_buf.insert(raw_words, offset=window_start_sec)
            committed_now = hyp_buf.flush()

            for _, _, w in committed_now:
                committed_words.append(w)

            if committed_now:
                print(
                    f"  ✓ +{len(committed_now)} "
                    f"[{' '.join(w for _,_,w in committed_now)}] "
                    f"total={len(committed_words)}"
                )

            pending = [w for _, _, w in hyp_buf.complete()]

            # ── 8. Trim speech_buf (только для памяти) ────────────────────────
            # Независимо от того что подаётся в модель — буфер можно тримить
            # когда он слишком большой. Это не влияет на correctness.
            buf_dur = len(speech_buf) / SAMPLE_RATE
            if buf_dur > BUFFER_TRIM_SEC and hyp_buf.last_committed_time > buffer_time_offset:
                # Тримим до (committed_end − OVERLAP), с запасом
                trim_to_sec   = max(
                    buffer_time_offset,
                    hyp_buf.last_committed_time - OVERLAP_SEC - 1.0  # доп. запас
                )
                trim_samples  = int((trim_to_sec - buffer_time_offset) * SAMPLE_RATE)
                if trim_samples > 0:
                    speech_buf         = speech_buf[trim_samples:]
                    buffer_time_offset = trim_to_sec
                    hyp_buf.pop_committed(trim_to_sec)
                    print(
                        f"  [TRIM] buf→{len(speech_buf)/SAMPLE_RATE:.1f}s "
                        f"offset={buffer_time_offset:.1f}s"
                    )

            await _send(websocket, committed_words, pending,
                        model_dt, avg_ms, n_calls, n_hallucinations)

    await asyncio.gather(receiver(), processor())


# ══════════════════════════════════════════════════════════════════════════════
#  ОТПРАВКА РЕЗУЛЬТАТА
# ══════════════════════════════════════════════════════════════════════════════

async def _send(
    ws:               WebSocket,
    committed:        list[str],
    pending:          list[str],
    model_time:       float,
    avg_model_ms:     float,
    n_calls:          int,
    n_hallucinations: int,
):
    speed_log = (
        f"model {model_time*1000:.0f}ms · "
        f"avg {avg_model_ms:.0f}ms · "
        f"calls={n_calls} hal={n_hallucinations}"
    )
    payload = {
        "stable":     " ".join(committed),
        "pending":    " ".join(pending),
        "chars":      len(" ".join(committed)),
        "speed_logs": speed_log,
        "task":       TASK,
        "lang":       LANGUAGE,
    }
    try:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
    except RuntimeError:
        print("[WS] connection closed")