"""
Пример клиента для Streaming TTS WebSocket
===========================================
pip install websockets sounddevice numpy
python client_example.py --ref_audio my_voice.wav --text "Привет мир"
"""

import argparse
import asyncio
import base64
import json
import struct
import sys

import numpy as np

try:
    import websockets
except ImportError:
    print("pip install websockets")
    sys.exit(1)


async def stream_tts(
    ref_audio_path: str,
    ref_text: str,
    lang: str,
    text: str,
    url: str = "ws://localhost:8000/api/generate",
    play_audio: bool = False,
    save_path: str | None = "output.wav",
):
    # Читаем ref_audio и кодируем в base64
    with open(ref_audio_path, "rb") as f:
        ref_audio_b64 = base64.b64encode(f.read()).decode()

    payload = {
        "ref_audio": ref_audio_b64,
        "ref_text":  ref_text,
        "lang":      lang,
        "text":      text,
    }

    all_pcm = bytearray()

    print(f"Подключаемся к {url}...")
    async with websockets.connect(url, max_size=50 * 1024 * 1024) as ws:
        await ws.send(json.dumps(payload))
        print("Запрос отправлен. Ожидаем аудио...\n")

        chunk_idx = 0
        while True:
            msg = await ws.recv()

            if isinstance(msg, bytes):
                # Парсим: первые 4 байта = длина PCM
                pcm_len = struct.unpack("<I", msg[:4])[0]
                pcm_bytes = msg[4:4 + pcm_len]
                all_pcm.extend(pcm_bytes)

                samples = np.frombuffer(pcm_bytes, dtype=np.int16)
                duration = len(samples) / 24000
                chunk_idx += 1
                print(f"  Чанк {chunk_idx}: {len(samples)} сэмплов ({duration:.2f}с)")

                if play_audio:
                    _play_pcm(samples)

            elif isinstance(msg, str):
                event = json.loads(msg)
                if event.get("event") == "done":
                    print(f"\n✅ Готово: {event['chunks']} чанков за {event['elapsed']}с")
                    break
                elif event.get("event") == "error":
                    print(f"\n❌ Ошибка: {event['message']}")
                    break

    # Сохраняем результат
    if save_path and all_pcm:
        import soundfile as sf
        audio = np.frombuffer(all_pcm, dtype=np.int16).astype(np.float32) / 32767
        sf.write(save_path, audio, 24000)
        print(f"Сохранено: {save_path}")


def _play_pcm(samples: np.ndarray):
    try:
        import sounddevice as sd
        audio_f32 = samples.astype(np.float32) / 32767
        sd.play(audio_f32, samplerate=24000, blocking=True)
    except Exception as e:
        print(f"  [воспроизведение]: {e}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ref_audio", required=True)
    p.add_argument("--ref_text",  default="")
    p.add_argument("--lang",      default="Russian")
    p.add_argument("--text",      default="Привет! Это тест потокового синтеза речи.")
    p.add_argument("--url",       default="ws://localhost:8000/api/generate")
    p.add_argument("--play",      action="store_true", help="Воспроизводить аудио чанки")
    p.add_argument("--out",       default="output.wav")
    args = p.parse_args()

    asyncio.run(stream_tts(
        ref_audio_path=args.ref_audio,
        ref_text=args.ref_text,
        lang=args.lang,
        text=args.text,
        url=args.url,
        play_audio=args.play,
        save_path=args.out,
    ))