import torch
import numpy as np


class VADProcessor:
    def __init__(self, model_path: str = "models/silero_vad.jit",
                 device: str = "cpu", sample_rate: int = 16000):
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        self.sample_rate = sample_rate
        self.window_size = 512  # 32 мс при 16 кГц
        self.model = self._load_model(model_path)
        self.model.to(self.device).eval()

    def _load_model(self, model_path: str) -> torch.nn.Module:
        try:
            return torch.jit.load(model_path)
        except Exception:
            model, _ = torch.hub.load(
                repo_or_dir='snakers4/silero-vad', model='silero_vad',
                force_reload=False, verbose=False, trust_repo=True
            )
            return model

    # ── Новый метод: работает с float32, не трогает int16 ──────────────────
    def filter_chunk_float32(
        self, audio: np.ndarray, threshold: float = 0.5
    ) -> tuple[np.ndarray, bool]:
        """
        Принимает float32-чанк ([-1, 1]), возвращает:
          - filtered: тот же массив, но тихие 512-сэмпл-окна обнулены
          - has_speech: True если хотя бы одно окно содержит речь

        Важно: в filtered сохраняется исходная громкость и длина массива —
        скользящее окно Whisper остаётся временно выровненным.
        """
        result = np.zeros_like(audio)
        has_speech = False
        n = len(audio)

        for i in range(0, n, self.window_size):
            window = audio[i: i + self.window_size]

            # Неполное последнее окно — копируем как есть, не гоним через VAD
            # (512 сэмплов = 32 мс; хвост меньше — погрешность незначительна)
            if len(window) < self.window_size:
                result[i: i + len(window)] = window
                break

            max_val = np.max(np.abs(window))
            if max_val < 1e-6:
                # Абсолютная тишина — пропускаем инференс
                continue

            # Нормализуем только для инференса, в result кладём оригинал
            window_norm = (window / max_val).astype(np.float32)
            tensor = torch.from_numpy(window_norm).to(self.device)

            with torch.no_grad():
                prob = self.model(tensor.unsqueeze(0), self.sample_rate).item()

            if prob > threshold:
                result[i: i + self.window_size] = window
                has_speech = True

        return result, has_speech

    def extract_speech_float32(
        self, audio: np.ndarray, threshold: float = 0.5
    ) -> tuple[np.ndarray, bool]:
        """
        Возвращает только окна со речью, склеенные подряд.
        Тишина физически вырезается — массив короче входного.
        """
        speech_windows = []
    
        for i in range(0, len(audio), self.window_size):
            window = audio[i: i + self.window_size]
    
            # Неполное последнее окно — берём без инференса
            if len(window) < self.window_size:
                if len(speech_windows):  # добавляем только если уже есть речь в чанке
                    speech_windows.append(window)
                break
    
            max_val = np.max(np.abs(window))
            if max_val < 1e-6:
                continue  # абсолютная тишина — пропускаем даже инференс
    
            window_norm = (window / max_val).astype(np.float32)
            tensor = torch.from_numpy(window_norm).to(self.device)
            with torch.no_grad():
                prob = self.model(tensor.unsqueeze(0), self.sample_rate).item()
    
            if prob > threshold:
                speech_windows.append(window)
    
        if speech_windows:
            return np.concatenate(speech_windows), True
        return np.array([], dtype=np.float32), False

    def reset_states(self) -> None:
        """Сбрасывает внутренние состояния Silero между предложениями."""
        try:
            self.model.reset_states()
        except Exception:
            pass  # JIT-версия может не иметь этого метода

    # ── Старые методы оставлены для обратной совместимости ─────────────────
    def _bytes_to_audio(self, audio_bytes: bytes, chunk_size: int = None) -> np.ndarray:
        audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
        audio_float = audio_int16.astype(np.float32) / 32768.0
        if chunk_size and len(audio_float) != chunk_size:
            if len(audio_float) < chunk_size:
                audio_float = np.pad(audio_float, (0, chunk_size - len(audio_float)))
            else:
                audio_float = audio_float[:chunk_size]
        return audio_float

    def _audio_to_bytes(self, audio: np.ndarray) -> bytes:
        return (audio * 32767).astype(np.int16).tobytes()

    def process_chunk(self, audio_bytes: bytes, threshold: float = 0.5,
                      chunk_size: int = 512) -> bytes:
        audio = self._bytes_to_audio(audio_bytes, chunk_size)
        if np.max(np.abs(audio)) > 0:
            audio_norm = audio / np.max(np.abs(audio))
        else:
            return b''
        audio_tensor = torch.from_numpy(audio_norm).to(self.device)
        with torch.no_grad():
            speech_prob = self.model(audio_tensor.unsqueeze(0), self.sample_rate).item()
        mask = 1.0 if speech_prob > threshold else 0.0
        return self._audio_to_bytes(audio * mask)

    def process_stream(self, audio_bytes_stream: bytes, threshold: float = 0.5,
                       chunk_size: int = 512) -> bytes:
        if len(audio_bytes_stream) < chunk_size * 2:
            return self.process_chunk(audio_bytes_stream, threshold, chunk_size)
        cleaned_chunks = []
        for i in range(0, len(audio_bytes_stream), chunk_size * 2):
            chunk_bytes = audio_bytes_stream[i: i + chunk_size * 2]
            if len(chunk_bytes) == chunk_size * 2:
                cleaned_chunks.append(self.process_chunk(chunk_bytes, threshold, chunk_size))
        return b''.join(cleaned_chunks)