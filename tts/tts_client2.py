import asyncio
import base64
import json
import struct

import numpy as np
import sounddevice as sd
import websockets


WS_URL = "ws://localhost:8000/api/generate"
SAMPLE_RATE = 24000


async def stream_tts(ref_audio_path, ref_text, lang, text):
    # читаем референс
    with open(ref_audio_path, "rb") as f:
        ref_audio_b64 = base64.b64encode(f.read()).decode()

    payload = {
        "ref_audio": ref_audio_b64,
        "ref_text": ref_text,
        "lang": lang,
        "text": text,
    }

    # очередь аудио чанков
    audio_queue = asyncio.Queue()

    # callback для sounddevice
    def audio_callback(outdata, frames, time, status):
        try:
            chunk = audio_queue.get_nowait()
        except asyncio.QueueEmpty:
            outdata.fill(0)
            return

        # если чанк меньше буфера — дополняем нулями
        if len(chunk) < frames:
            out = np.zeros(frames, dtype=np.float32)
            out[:len(chunk)] = chunk
        else:
            out = chunk[:frames]

        outdata[:, 0] = out

    # поток воспроизведения
    stream = sd.OutputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        callback=audio_callback,
        blocksize=1024,
    )

    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.send(json.dumps(payload))

        stream.start()

        try:
            while True:
                msg = await ws.recv()

                # бинарный чанк
                if isinstance(msg, bytes):
                    # первые 4 байта — длина
                    size = struct.unpack("<I", msg[:4])[0]
                    pcm = msg[4:4+size]

                    # int16 → float32
                    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

                    await audio_queue.put(audio)

                else:
                    data = json.loads(msg)

                    if data.get("event") == "done":
                        print("DONE:", data)
                        break

                    if data.get("event") == "error":
                        print("ERROR:", data)
                        break

        finally:
            await asyncio.sleep(1)  # доиграть буфер
            stream.stop()
            stream.close()


if __name__ == "__main__":
    asyncio.run(stream_tts(
        ref_audio_path="ref.wav",
        ref_text="пример текста",
        lang="Russian",
        text="Это тест синтеза речи через потоковый сервер. Проверяем как идет стриминг.",
    ))