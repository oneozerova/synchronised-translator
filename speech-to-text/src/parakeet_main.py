"""
Потоковый STT-бэкенд на базе nvidia/nemotron-speech-streaming-en-0.6b.

Архитектура: Cache-Aware FastConformer-RNNT.
Каждый чанк обрабатывается ровно один раз (без overlap).
Кэш self-attention и conv-слоёв переносится между шагами через conformer_stream_step().

Параметры задержки (att_context_size = [70, LOOKAHEAD]):
    LOOKAHEAD = 0  →  1 фрейм  =  80 ms
    LOOKAHEAD = 1  →  2 фрейма = 160 ms
    LOOKAHEAD = 6  →  7 фреймов = 560 ms
    LOOKAHEAD = 13 → 14 фреймов = 1120 ms   ← используется по умолчанию
"""

import asyncio
import json
import time
from functools import partial

import numpy as np
import torch
import nemo.collections.asr as nemo_asr
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from src.VAD_processing import VADProcessor


# ─── Параметры ───────────────────────────────────────────────────────────────
VAD_THRESHOLD   = 0.5
SAMPLE_RATE     = 16000
SILENCE_TIMEOUT = 0.5   # сек тишины → флаш остатка буфера + сброс состояния

# Задержка: меняй только LOOKAHEAD
LOOKAHEAD         = 5                                          # правый контекст в фреймах
ENCODER_STEP_MS   = 80                                          # мс на 1 выходной фрейм
CHUNK_FRAMES      = LOOKAHEAD + 1                               # 14 фреймов
SAMPLES_PER_FRAME = int(ENCODER_STEP_MS * SAMPLE_RATE / 1000)  # 1280 сэмплов / фрейм
CHUNK_SAMPLES     = CHUNK_FRAMES * SAMPLES_PER_FRAME            # 17920 сэмплов = 1120 мс

PENDING_WORDS = 4   # последние N слов идут в "pending" для UI


# ─── Инициализация модели ─────────────────────────────────────────────────────
asr_model: nemo_asr.models.ASRModel = (
    nemo_asr.models.ASRModel
    .from_pretrained("nvidia/nemotron-speech-streaming-en-0.6b")
    .cpu()
    .eval()
)

# Задержка / точность — меняется без переобучения
asr_model.encoder.set_default_att_context_size([70, LOOKAHEAD])

# Отключаем случайный dither и pad препроцессора
asr_model.preprocessor.featurizer.dither = 0.0
asr_model.preprocessor.featurizer.pad_to = 0

# Число лишних фреймов после субсэмплинга, которые отбрасываются со 2-го шага
_DROP_EXTRA: int = getattr(
    asr_model.encoder.streaming_cfg, "drop_extra_pre_encoded", 0
)

vad = VADProcessor(device="cpu")
# ─────────────────────────────────────────────────────────────────────────────


# ─── Вспомогательные функции ──────────────────────────────────────────────────
def preprocess_audio(chunk: np.ndarray) -> np.ndarray:
    """DC-offset и нормализация амплитуды."""
    chunk = chunk - np.mean(chunk)
    return (chunk / (np.max(np.abs(chunk)) + 1e-6)).astype(np.float32)


@torch.inference_mode()
def features_from_audio(audio: np.ndarray) -> tuple[torch.Tensor, torch.Tensor]:
    """
    float32 numpy [T] → log-mel фичи [1, n_mels, T'] + длины.
    Вызывается препроцессором модели (без dither, без pad).
    """
    sig     = torch.from_numpy(audio).unsqueeze(0).cpu()
    sig_len = torch.tensor([len(audio)], device="cpu")
    feats, feat_len = asr_model.preprocessor(input_signal=sig, length=sig_len)
    return feats, feat_len


def _split_stable_pending(text: str) -> tuple[str, str]:
    """Разделяет текст на стабильную часть и последние PENDING_WORDS слов."""
    words = text.split()
    if len(words) <= PENDING_WORDS:
        return "", text
    return " ".join(words[:-PENDING_WORDS]), " ".join(words[-PENDING_WORDS:])


# ─── Состояние одного стримингового сеанса ────────────────────────────────────
class StreamState:
    """
    Инкапсулирует всё изменяемое состояние одного WebSocket-соединения.

    Кэши энкодера (переносятся между шагами):
        cache_last_channel     — активации conv-слоёв  [N_layers, 1, C, conv_cache]
        cache_last_time        — KV self-attention      [N_layers, 1, D, att_cache]
        cache_last_channel_len — длины conv-кэша        [N_layers, 1]

    Состояние декодера:
        previous_hypotheses    — луч RNNT (объекты Hypothesis)
        pred_out_stream        — выходной тензор предыдущего шага

    Буферы:
        audio_buf  — накопленное аудио (float32), ждущее полного чанка
        full_text  — весь распознанный текст с начала сессии (только растёт)
    """

    def __init__(self):
        self.audio_buf  = np.array([], dtype=np.float32)
        self.full_text  = ""
        self.last_voice = time.monotonic()
        self._reset_encoder()

    def _reset_encoder(self):
        """Сбрасывает кэши и состояние декодера (вызывается между фразами)."""
        (
            self.cache_last_channel,
            self.cache_last_time,
            self.cache_last_channel_len,
        ) = asr_model.encoder.get_initial_cache_state(batch_size=1)
        self.previous_hypotheses = None
        self.pred_out_stream     = None
        self.step_num            = 0
        # full_text намеренно НЕ сбрасывается

    @torch.inference_mode()
    def step(self, audio_chunk: np.ndarray, keep_all_outputs: bool = False) -> str:
        """
        Один шаг: audio_chunk (float32, ровно CHUNK_SAMPLES сэмплов) → текст.

        keep_all_outputs=True используется при флаше (конец фразы):
            декодер отдаёт все хвостовые токены, не ожидая правого контекста.

        Возвращает полный накопленный текст (cumulative).
        """
        feats, feat_len = features_from_audio(audio_chunk)

        # Шаг 0: pre-encode кэш ещё пуст → лишних фреймов нет → drop=0
        drop = 0 if self.step_num == 0 else _DROP_EXTRA

        (
            self.pred_out_stream,
            transcribed_texts,
            self.cache_last_channel,
            self.cache_last_time,
            self.cache_last_channel_len,
            self.previous_hypotheses,
        ) = asr_model.conformer_stream_step(
            processed_signal        = feats,
            processed_signal_length = feat_len,
            cache_last_channel      = self.cache_last_channel,
            cache_last_time         = self.cache_last_time,
            cache_last_channel_len  = self.cache_last_channel_len,
            keep_all_outputs        = keep_all_outputs,
            previous_hypotheses     = self.previous_hypotheses,
            previous_pred_out       = self.pred_out_stream,
            drop_extra_pre_encoded  = drop,
        )
        self.step_num += 1

        if transcribed_texts and transcribed_texts[0].text.strip():
            self.full_text = transcribed_texts[0].text.strip()

        return self.full_text

    def flush(self) -> str:
        """
        Флаш остатка буфера при наступлении тишины.
        Дополняет аудио тишиной до CHUNK_SAMPLES и форвардит с keep_all_outputs=True,
        чтобы декодер выдал все оставшиеся токены.
        """
        if len(self.audio_buf) == 0:
            return self.full_text

        padded = np.zeros(CHUNK_SAMPLES, dtype=np.float32)
        padded[: len(self.audio_buf)] = self.audio_buf
        self.audio_buf = np.array([], dtype=np.float32)

        return self.step(padded, keep_all_outputs=True)

    def on_silence(self) -> bool:
        """
        Вызывается когда тишина превысила SILENCE_TIMEOUT.
        Сбрасывает энкодер/декодер для следующей фразы.
        Возвращает True если нужен flush перед сбросом.
        """
        needs_flush = len(self.audio_buf) > 0
        self._reset_encoder()
        return needs_flush
# ─────────────────────────────────────────────────────────────────────────────


app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue()
    state = StreamState()

    # ── Receiver: читает байты из WebSocket ──────────────────────────────────
    async def receiver():
        try:
            while True:
                raw = await websocket.receive_bytes()
                print(len(raw))
                await queue.put(raw)
        except WebSocketDisconnect:
            await queue.put(None)

    # ── Processor: VAD + чанкинг + conformer_stream_step ─────────────────────
    async def processor():
        loop              = asyncio.get_event_loop()
        silence_triggered = False   # флаг: сброс уже выполнен для текущей тишины

        while True:
            raw = await queue.get()
            if raw is None:
                # Соединение закрыто — флашим остаток если есть
                if len(state.audio_buf) > 0:
                    text = await loop.run_in_executor(None, state.flush)
                    await _send(websocket, text)
                break

            t0  = time.monotonic()
            now = t0

            chunk = np.frombuffer(raw, dtype=np.float32)
            chunk = preprocess_audio(chunk)

            # ── VAD в executor (не блокирует event loop) ──────────────────────
            speech_chunk, speech = await loop.run_in_executor(
                None, vad.extract_speech_float32, chunk, VAD_THRESHOLD
            )

            if speech:
                state.last_voice  = now
                silence_triggered = False
                state.audio_buf   = np.concatenate([state.audio_buf, speech_chunk])

                # ── Обрабатываем все полные чанки из буфера ──────────────────
                while len(state.audio_buf) >= CHUNK_SAMPLES:
                    chunk_to_process = state.audio_buf[:CHUNK_SAMPLES]
                    state.audio_buf  = state.audio_buf[CHUNK_SAMPLES:]

                    m0        = time.monotonic()
                    full_text = await loop.run_in_executor(
                        None, partial(state.step, chunk_to_process, False)
                    )
                    m1 = time.monotonic()

                    await _send(websocket, full_text, t0, time.monotonic(), m0, m1)

            else:
                # ── Тишина ───────────────────────────────────────────────────
                elapsed_silence = now - state.last_voice

                if elapsed_silence > SILENCE_TIMEOUT and not silence_triggered:
                    silence_triggered = True

                    # Флаш остатка буфера (последние слова фразы)
                    if len(state.audio_buf) > 0:
                        m0        = time.monotonic()
                        full_text = await loop.run_in_executor(None, state.flush)
                        m1        = time.monotonic()
                        await _send(websocket, full_text, t0, time.monotonic(), m0, m1)

                    # Сброс кэшей энкодера и декодера для следующей фразы
                    # full_text сохраняется — текст продолжается
                    state._reset_encoder()


    await asyncio.gather(receiver(), processor())


async def _send(
    ws: WebSocket,
    full_text: str,
    t0: float | None = None,
    t1: float | None = None,
    m0: float | None = None,
    m1: float | None = None,
) -> None:
    stable, pending = _split_stable_pending(full_text)

    speed = ""
    if t0 and t1:
        speed = f"full={t1 - t0:.3f}s"
        if m0 and m1:
            speed += f" | model={m1 - m0:.3f}s"
    if speed:
        print(speed)

    try:
        await ws.send_text(json.dumps({
            "stable":     stable,
            "pending":    pending,
            "chars":      len(full_text),
            "speed_logs": speed,
        }))
    except RuntimeError:
        print("STOP")