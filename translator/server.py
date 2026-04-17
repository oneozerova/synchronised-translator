import time
import json
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from faster_whisper import WhisperModel

model = WhisperModel("tiny", device="cpu", compute_type="int8")


def transcribe(audio, prompt=""):
    segments, _ = model.transcribe(
        audio,
        language="en",
        vad_filter=False,
        initial_prompt=prompt or None,
    )
    return " ".join(s.text for s in segments).strip()


SAMPLE_RATE = 16000
WINDOW_SEC = 1.5
WINDOW = int(SAMPLE_RATE * WINDOW_SEC)

SILENCE_TIMEOUT = 1.0
PENDING_WORDS = 3
PROMPT_WORDS = 10


def is_speech(x):
    return True
    if np.sqrt(np.mean(x ** 2)) > 0.01:
        return True
    else:
        print(np.sqrt(np.mean(x ** 2)))
        return False


def norm(text):
    return [w.lower().strip(".,!?…") for w in text.split() if w]


def build_prompt(words):
    return " ".join(words[-PROMPT_WORDS:])


app = FastAPI()


@app.websocket("/ws")
async def ws(ws: WebSocket):
    await ws.accept()

    audio_window = np.array([], dtype=np.float32)

    committed = []
    last_candidates = []
    stable_counter = {}

    last_voice = time.time()

    try:
        while True:
            full_time_start = time.time()
            raw = await ws.receive_bytes()
            chunk = np.frombuffer(raw, dtype=np.float32)

            now = time.time()
            audio_window = np.concatenate([audio_window, chunk])[-WINDOW:]

            speech = is_speech(chunk)

            if speech:
                last_voice = now

                model_processing_start = time.time()
                text = transcribe(audio_window, prompt=build_prompt(committed))
                model_processing_end = time.time()

                if not text:
                    continue

                words = norm(text)
                if not words:
                    continue

                new_stable = []

                for w in words:
                    stable_counter[w] = stable_counter.get(w, 0) + 1

                    if stable_counter[w] >= 2 and w not in committed:
                        new_stable.append(w)

                if new_stable:
                    committed.extend(new_stable)

                last_candidates = words

            else:
                print("SKIP")
                # silence endpointing
                if now - last_voice > SILENCE_TIMEOUT:
                    # flush last uncertain words
                    if last_candidates:
                        for w in last_candidates[-PENDING_WORDS:]:
                            if w not in committed:
                                committed.append(w)

                    last_candidates = []
                    stable_counter = {}
                    audio_window = np.array([], dtype=np.float32)
            full_time_end = time.time()

            time_text = f"full time - {full_time_end - full_time_start}"
            if speech:
                time_text += f" | model_time - {model_processing_end - model_processing_start}"
            
            print(time_text)

            await ws.send_text(json.dumps({
                "stable": " ".join(committed),
                "pending": " ".join(last_candidates[-PENDING_WORDS:]),
                "chars": len(" ".join(committed)),
                "speed_logs": time_text
            }))
                
    except WebSocketDisconnect:
        pass