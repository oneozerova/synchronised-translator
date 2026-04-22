import base64
import json

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Live Translator + TTS", page_icon="🎧")
st.title("🎧 Live Translator + TTS")
st.caption("Аудио -> STT -> Перевод -> TTS (воспроизведение в реальном времени)")
model_choice = st.selectbox(
    "STT модель",
    ["whisper-small", "voxtral"],
)

MODEL = {json.dumps(model_choice)}
WS_URL = "ws://0.0.0.0:8000/ws"

uploaded = st.file_uploader(
    "Аудиофайл (если не выбран, используется микрофон)",
    type=["wav", "mp3", "m4a", "ogg", "webm", "flac"],
)

ref_uploaded = st.file_uploader(
    "Референсное аудио для TTS (WAV)",
    type=["wav"],
)

ref_text_value = st.text_input(
    "Референсная фраза для TTS",
    value="Это тестовая референсная фраза.",
)



audio_b64 = ""
audio_mime = ""
audio_name = "микрофон"
ref_audio_b64 = ""
ref_audio_name = "тишина (по умолчанию)"

if uploaded is not None:
    audio_bytes = uploaded.getvalue()
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
    audio_mime = uploaded.type
    audio_name = uploaded.name

if ref_uploaded is not None:
    ref_audio_bytes = ref_uploaded.getvalue()
    ref_audio_b64 = base64.b64encode(ref_audio_bytes).decode("ascii")
    ref_audio_name = ref_uploaded.name

components.html(
    f"""
<style>
  body {{ font-family: sans-serif; margin: 0; }}
  #btn {{
    padding: 10px 20px;
    border: 0;
    border-radius: 8px;
    background: #2563eb;
    color: #fff;
    cursor: pointer;
  }}
  #btn.active {{ background: #374151; }}
  #status {{ margin-top: 8px; color: #555; font-size: 14px; }}
  #translated {{
    margin-top: 12px;
    min-height: 120px;
    border: 1px solid #ddd;
    border-radius: 8px;
    padding: 10px;
    background: #fafafa;
    white-space: pre-wrap;
  }}
  #log {{
    margin-top: 10px;
    font-size: 12px;
    color: #666;
    min-height: 48px;
  }}
</style>

<button id="btn" onclick="toggle()">Start</button>
<div id="status">Idle</div>
<div style="margin-top:8px; font-size:13px;">Источник: {audio_name}</div>
<div style="margin-top:4px; font-size:13px;">TTS reference: {ref_audio_name}</div>
<div id="translated"></div>
<div id="log"></div>

<script>
const WS_URL = "{WS_URL}";
const HAS_FILE = {str(bool(audio_b64)).lower()};
const AUDIO_B64 = {json.dumps(audio_b64)};
const REF_AUDIO_B64 = {json.dumps(ref_audio_b64)};
const REF_TEXT = {json.dumps(ref_text_value)};
const MODEL = {json.dumps(model_choice)};

const INPUT_SAMPLE_RATE = 16000;
const CHUNK_SEC = 0.25;
const TTS_DEFAULT_SR = 24000;

let ws = null;
let recording = false;
let inputCtx = null;
let micStream = null;
let micSource = null;
let processor = null;
let inputBuffer = [];

let playbackCtx = null;
let playbackSampleRate = TTS_DEFAULT_SR;
let nextPlaybackTime = 0;

function setStatus(text) {{
  document.getElementById("status").textContent = text;
}}

function log(text) {{
  document.getElementById("log").textContent = text;
}}

function setTranslated(text) {{
  document.getElementById("translated").textContent = text || "";
}}

function setBtn(active) {{
  const btn = document.getElementById("btn");
  btn.textContent = active ? "Stop" : "Start";
  btn.className = active ? "active" : "";
}}

function toggle() {{
  if (recording) {{
    stop();
  }} else {{
    start();
  }}
}}

async function ensurePlaybackContext() {{
  if (!playbackCtx) {{
    playbackCtx = new (window.AudioContext || window.webkitAudioContext)({{ sampleRate: playbackSampleRate }});
  }}
  await playbackCtx.resume();
  if (!nextPlaybackTime || nextPlaybackTime < playbackCtx.currentTime) {{
    nextPlaybackTime = playbackCtx.currentTime + 0.08;
  }}
}}

function pcm16ToFloat32(int16) {{
  const out = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) {{
    out[i] = int16[i] / 32768;
  }}
  return out;
}}

async function schedulePcmFrame(buffer) {{
  await ensurePlaybackContext();
  const view = new DataView(buffer);
  const pcmLen = view.getUint32(8, true);
  const pcm = new Int16Array(buffer, 12, pcmLen / 2);
  const float32 = pcm16ToFloat32(pcm);

  const audioBuffer = playbackCtx.createBuffer(1, float32.length, playbackSampleRate);
  audioBuffer.copyToChannel(float32, 0);
  const src = playbackCtx.createBufferSource();
  src.buffer = audioBuffer;
  src.connect(playbackCtx.destination);

  const startAt = Math.max(nextPlaybackTime, playbackCtx.currentTime + 0.02);
  src.start(startAt);
  nextPlaybackTime = startAt + audioBuffer.duration;
}}

function base64ToUint8Array(b64) {{
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {{
    bytes[i] = binary.charCodeAt(i);
  }}
  return bytes;
}}

async function streamFile() {{
  const bytes = base64ToUint8Array(AUDIO_B64);
  const decodeCtx = new AudioContext();
  const decoded = await decodeCtx.decodeAudioData(bytes.buffer.slice(0));
  const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * INPUT_SAMPLE_RATE), INPUT_SAMPLE_RATE);
  const src = offline.createBufferSource();
  src.buffer = decoded;
  src.connect(offline.destination);
  src.start();
  const rendered = await offline.startRendering();
  const samples = rendered.getChannelData(0);

  const chunkSamples = Math.floor(INPUT_SAMPLE_RATE * CHUNK_SEC);
  for (let i = 0; i < samples.length && recording; i += chunkSamples) {{
    const end = Math.min(i + chunkSamples, samples.length);
    const chunk = new Float32Array(samples.subarray(i, end));
    if (ws && ws.readyState === WebSocket.OPEN) {{
      ws.send(chunk.buffer);
    }}
    await new Promise((resolve) => setTimeout(resolve, CHUNK_SEC * 1000));
  }}
}}

async function startMic() {{
  micStream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
  inputCtx = new AudioContext({{ sampleRate: INPUT_SAMPLE_RATE }});
  micSource = inputCtx.createMediaStreamSource(micStream);
  processor = inputCtx.createScriptProcessor(4096, 1, 1);
  const chunkSamples = Math.floor(INPUT_SAMPLE_RATE * CHUNK_SEC);

  processor.onaudioprocess = (e) => {{
    inputBuffer.push(...e.inputBuffer.getChannelData(0));
    while (inputBuffer.length >= chunkSamples) {{
      const chunk = new Float32Array(inputBuffer.splice(0, chunkSamples));
      if (ws && ws.readyState === WebSocket.OPEN) {{
        ws.send(chunk.buffer);
      }}
    }}
  }};

  micSource.connect(processor);
  processor.connect(inputCtx.destination);
}}

async function start() {{
  recording = true;
  setBtn(true);
  setStatus("Connecting...");
  setTranslated("");
  log("");

  await ensurePlaybackContext();

  ws = new WebSocket(WS_URL);
  ws.binaryType = "arraybuffer";

  ws.onopen = async () => {{
    ws.send(JSON.stringify({{
      event: "start",
      model: MODEL,
      ref_audio: REF_AUDIO_B64 || "",
      ref_text: REF_TEXT || "",
      lang: "Russian",
    }}));

    setStatus("Streaming...");
    if (HAS_FILE) {{
      await streamFile();
      setStatus("File sent, waiting for TTS...");
    }} else {{
      await startMic();
    }}
  }};

  ws.onmessage = async (event) => {{
    if (typeof event.data === "string") {{
      let payload = null;
      try {{
        payload = JSON.parse(event.data);
      }} catch {{
        return;
      }}

      if (payload.event === "translation" || payload.stable !== undefined) {{
        setTranslated(payload.stable || "");
        return;
      }}
      if (payload.event === "started" && payload.sample_rate) {{
        playbackSampleRate = Number(payload.sample_rate) || TTS_DEFAULT_SR;
        log("TTS started: sample_rate=" + playbackSampleRate);
        return;
      }}
      if (payload.event === "chunk_begin") {{
        log("TTS chunk " + payload.seq + " started");
        return;
      }}
      if (payload.event === "chunk_end") {{
        log("TTS chunk " + payload.seq + " done");
        return;
      }}
      if (payload.event === "done") {{
        setStatus("Done");
        return;
      }}
      if (payload.event === "error") {{
        setStatus("Error");
        log(payload.message || "unknown error");
      }}
      return;
    }}

    await schedulePcmFrame(event.data);
  }};

  ws.onerror = () => {{
    setStatus("WebSocket error");
  }};

  ws.onclose = () => {{
    if (recording) {{
      setStatus("Closed");
    }}
  }};
}}

function stop() {{
  recording = false;
  setBtn(false);
  setStatus("Stopped");
  inputBuffer = [];

  if (processor) processor.disconnect();
  if (micSource) micSource.disconnect();
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  if (inputCtx) inputCtx.close();
  processor = null;
  micSource = null;
  micStream = null;
  inputCtx = null;

  if (ws) ws.close();
  ws = null;
}}
</script>
""",
    height=500,
)