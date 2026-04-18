import torch
import numpy as np


class VADProcessor:
    """VAD процессор для обработки аудио-чанков"""
    
    def __init__(self, model_path: str = "models/silero_vad.jit", 
                 device: str = "cpu", sample_rate: int = 16000):
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        self.sample_rate = sample_rate
        self.window_size = 512
        self.model = self._load_model(model_path)
        self.model.to(self.device).eval()
    
    def _load_model(self, model_path: str) -> torch.nn.Module:
        """Загрузка модели с fallback"""
        try:
            return torch.jit.load(model_path)
        except:
            model, _ = torch.hub.load(
                repo_or_dir='snakers4/silero-vad', model='silero_vad', 
                force_reload=False, verbose=False
            )
            return model
    
    def _bytes_to_audio(self, audio_bytes: bytes, chunk_size: int = None) -> np.ndarray:
        """Конвертация байтов в аудио"""
        audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
        audio_float = audio_int16.astype(np.float32) / 32768.0  # Нормализация [-1, 1]
        
        if chunk_size and len(audio_float) != chunk_size:
            if len(audio_float) < chunk_size:
                audio_float = np.pad(audio_float, (0, chunk_size - len(audio_float)))
            else:
                audio_float = audio_float[:chunk_size]
        
        return audio_float
    
    def _audio_to_bytes(self, audio: np.ndarray) -> bytes:
        """float32 -> int16 -> bytes"""
        audio_int16 = (audio * 32767).astype(np.int16)
        return audio_int16.tobytes()
    
    def process_chunk(self, audio_bytes: bytes, threshold: float = 0.5, 
                     chunk_size: int = 512) -> bytes:
        """
        Основная функция для обработки чанка аудио.        
        Args:
            audio_bytes: сырые байты аудио (int16, 16kHz)
            threshold: порог VAD
            chunk_size: ожидаемый размер чанка
            
        Returns:
            Обработанные байты (речь сохранена, тишина -> 0)
        """
        audio = self._bytes_to_audio(audio_bytes, chunk_size)
        
        if np.max(np.abs(audio)) > 0:
            audio_norm = audio / np.max(np.abs(audio))
        else:
            return b''
        
        audio_tensor = torch.from_numpy(audio_norm).to(self.device)
        
        with torch.no_grad():
            speech_prob = self.model(audio_tensor.unsqueeze(0), self.sample_rate).item()
        
        mask = 1.0 if speech_prob > threshold else 0.0
        cleaned_audio = audio * mask
        
        return self._audio_to_bytes(cleaned_audio)
    
    def process_stream(self, audio_bytes_stream: bytes, threshold: float = 0.5, 
                      chunk_size: int = 512) -> bytes:
        """
        Обработка потока байтов (разбивает на чанки)
        """
        if len(audio_bytes_stream) < chunk_size * 2:
            return self.process_chunk(audio_bytes_stream, threshold, chunk_size)
        
        cleaned_chunks = []
        for i in range(0, len(audio_bytes_stream), chunk_size * 2):
            chunk_bytes = audio_bytes_stream[i:i + chunk_size * 2]
            if len(chunk_bytes) == chunk_size * 2:
                cleaned_chunk = self.process_chunk(chunk_bytes, threshold, chunk_size)
                cleaned_chunks.append(cleaned_chunk)
        
        return b''.join(cleaned_chunks)
