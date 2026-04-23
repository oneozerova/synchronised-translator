# SimulStreaming Backend

FastAPI + WebSocket backend service for **real-time streaming speech-to-text translation**
powered by [SimulStreaming](https://github.com/ufal/SimulStreaming)
(Whisper large-v3 + EuroLLM 9B).

```
Browser / Client
     │  WebSocket (binary PCM + JSON results)
     ▼
FastAPI Backend  ← YOU ARE HERE
     │  TCP (raw PCM)            TCP (JSONL)
     ├──────────────► Whisper ──────────────► EuroLLM
     │                 (ASR)               (Translation)
     └◄──────────────────────────────────────────────────
              JSON results → WebSocket → client
```

---

## Prerequisites

| Component | Requirement |
|-----------|-------------|
| Python | 3.10+ |
| GPU | 1–2× NVIDIA, ≥10 GB VRAM (Whisper large-v3 + EuroLLM 9B) |
| SimulStreaming | cloned & installed separately |
| EuroLLM model | downloaded from HuggingFace + converted to CTranslate2 |

---

## Quick Start

### 1. Install SimulStreaming

```bash
git clone https://github.com/ufal/SimulStreaming
cd SimulStreaming
pip install -r requirements_whisper.txt
pip install -r requirements_translate.txt
```

Download and convert the EuroLLM model:
```bash
git clone https://huggingface.co/utter-project/EuroLLM-9B-Instruct
pip install transformers[torch]
ct2-transformers-converter \
    --model EuroLLM-9B-Instruct/ \
    --output_dir ct2_EuroLLM-9B-Instruct
```

### 2. Install backend

```bash
git clone <this-repo>
cd simulstreaming-backend
pip install -r requirements.txt
cp .env.example .env
# Edit .env – set correct paths, GPU settings, languages
```

### 3. Start SimulStreaming servers manually

Terminal 1 – Whisper ASR server:
```bash
cd ../SimulStreaming
python simulstreaming_whisper_server.py \
    --host 127.0.0.1 \
    --port 43001 \
    --model_path large-v3 \
    --lan auto \
    --task transcribe \
    --beams 5 \
    --vac \
    --min-chunk-size 1.0
```

Terminal 2 – LLM translation server:
```bash
cd ../SimulStreaming
python simulstreaming_translate_server.py \
    --host 127.0.0.1 \
    --port 43002 \
    --model-dir ct2_EuroLLM-9B-Instruct \
    --tokenizer-dir EuroLLM-9B-Instruct \
    --min-chunk-size 3
```

### 4. Start the backend

```bash
python server.py
```

Open `http://localhost:8000/demo` in your browser for a live demo.

---

## Or: Docker Compose (all-in-one)

> Requires Docker with NVIDIA Container Toolkit.

```bash
docker compose up --build
```

This starts:
- `whisper-server`  on port 43001
- `translate-server` on port 43002
- `backend` on port 8000

---

## WebSocket API

Connect to:
```
ws://localhost:8000/ws/translate?src_lang=en&tgt_lang=de&mode=cascade
```

**Query parameters:**

| Param | Values | Default | Description |
|-------|--------|---------|-------------|
| `src_lang` | `en`, `de`, `cs`, `auto`, … | `en` | Source language |
| `tgt_lang` | `de`, `fr`, `ru`, `uk`, … | `de` | Target language |
| `task` | `transcribe`, `translate` | `transcribe` | Whisper task |
| `mode` | `cascade`, `whisper` | `cascade` | `cascade`=ASR+LLM, `whisper`=ASR only |

**Client → Server:**
- **Binary frames**: raw PCM audio, S16_LE, 16 kHz, mono
- **Text frame `"DONE"`**: signals end of audio stream

**Server → Client (JSON text frames):**
```json
{
  "type":          "partial | final | error | info",
  "session_id":    "uuid",
  "src_text":      "recognized speech (partial)",
  "tgt_text":      "confirmed translation",
  "unconfirmed":   "translation in progress (may change)",
  "start":         0.0,
  "end":           1.5,
  "is_final":      false,
  "emission_time": 1.234
}
```

---

## Example Client

### Python (mic or file)

```bash
# Install extras
pip install websockets sounddevice numpy

# Stream from microphone (English → German)
python example_client.py --mic --src en --tgt de

# Stream from WAV file
python example_client.py --file audio.wav --src cs --tgt en

# ASR only (no translation)
python example_client.py --mic --mode whisper --src de

# Raw JSON output
python example_client.py --mic --verbose
```

### Minimal Python snippet

```python
import asyncio
import wave
import websockets
import json

async def translate_file(wav_path: str, src="en", tgt="de"):
    url = f"ws://localhost:8000/ws/translate?src_lang={src}&tgt_lang={tgt}&mode=cascade"

    async with websockets.connect(url) as ws:
        # Send audio
        with wave.open(wav_path) as wf:
            while chunk := wf.readframes(3200):
                await ws.send(chunk)
                await asyncio.sleep(0.2)          # simulate real-time
        await ws.send("DONE")

        # Receive results
        async for msg in ws:
            data = json.loads(msg)
            if data["type"] in ("partial", "final"):
                print(f"[{data['type']}] {data.get('tgt_text', data.get('src_text', ''))}")

asyncio.run(translate_file("speech.wav"))
```

### JavaScript / Browser (mic)

```javascript
const ws = new WebSocket(
  "ws://localhost:8000/ws/translate?src_lang=en&tgt_lang=de&mode=cascade"
);

// Receive results
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === "partial" || msg.type === "final") {
    console.log(`[${msg.type}]`, msg.tgt_text || msg.src_text);
  }
};

// Stream mic audio (after ws.onopen fires)
ws.onopen = async () => {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { sampleRate: 16000, channelCount: 1 },
  });
  const ctx = new AudioContext({ sampleRate: 16000 });
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(3200, 1, 1);

  proc.onaudioprocess = ({ inputBuffer }) => {
    const f32 = inputBuffer.getChannelData(0);
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      i16[i] = Math.round(Math.max(-1, Math.min(1, f32[i])) * 32767);
    }
    ws.send(i16.buffer);     // S16_LE PCM bytes
  };

  src.connect(proc);
  proc.connect(ctx.destination);
};
```

---

## HTTP API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Liveness probe – checks both TCP servers |
| `GET` | `/sessions` | List active WebSocket sessions |
| `GET` | `/sessions/{id}` | Session details |
| `DELETE` | `/sessions/{id}` | Force-close a session |
| `GET` | `/demo` | Browser demo UI |
| `GET` | `/docs` | OpenAPI docs (Swagger) |

---

## Configuration

All settings are read from environment variables (or `.env` file).
See `.env.example` for the full list.

Key settings:

```env
MANAGE_SUBPROCESSES=false    # true = backend spawns Whisper+Translate processes
WHISPER_HOST=127.0.0.1
WHISPER_PORT=43001
TRANSLATE_HOST=127.0.0.1
TRANSLATE_PORT=43002
WHISPER_MODEL_PATH=large-v3  # or /path/to/large-v3.pt
TRANSLATE_MODEL_DIR=ct2_EuroLLM-9B-Instruct
TRANSLATE_TOKENIZER_DIR=EuroLLM-9B-Instruct
```

---

## Architecture Notes

- Each WebSocket connection spawns **5 async coroutines** that pipeline through two TCP sockets.
- The backend is stateless – Whisper and Translate servers hold all model state.
- `mode=whisper` skips the Translate server entirely, useful for ASR-only use-cases.
- The `DONE` message gracefully drains both TCP sockets before closing.

---

## Supported Languages

**Source (Whisper):** 99 languages including auto-detection  
**Target (EuroLLM):** `ar bg ca cs da de el en es et fi fr ga gl hi hr hu it ja ko lt lv mt nl no pl pt ro ru sk sl sv tr uk zh`
