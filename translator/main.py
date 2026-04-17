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
WINDOW_SEC = 3
WINDOW = int(SAMPLE_RATE * WINDOW_SEC)

SILENCE_TIMEOUT = 1.0
PENDING_WORDS = 3
PROMPT_WORDS = 10


def is_speech(x):
    return np.sqrt(np.mean(x ** 2)) > 0.01


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
            raw = await ws.receive_bytes()
            chunk = np.frombuffer(raw, dtype=np.float32)

            now = time.time()
            audio_window = np.concatenate([audio_window, chunk])[-WINDOW:]

            speech = is_speech(chunk)

            if speech:
                last_voice = now

                text = transcribe(audio_window, prompt=build_prompt(committed))
                if not text:
                    continue

                words = norm(text)
                if not words:
                    continue

                # ─────────────────────────────
                # stability tracking (key fix)
                # ─────────────────────────────
                new_stable = []

                for w in words:
                    stable_counter[w] = stable_counter.get(w, 0) + 1

                    # слово считается стабильным только если появилось 2+ раз подряд
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

            await ws.send_text(json.dumps({
                "stable": " ".join(committed),
                "pending": " ".join(last_candidates[-PENDING_WORDS:]),
                "chars": len(" ".join(committed))
            }))

    except WebSocketDisconnect:
        pass