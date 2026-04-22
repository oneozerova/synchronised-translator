"""
Единый Streamlit frontend:
- отправка аудио (микрофон / файл) в backend websocket
- отображение streaming перевода
- воспроизведение streaming TTS
- поддержка reference audio / reference text для TTS
"""
import base64
import json

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Live Translator", page_icon="🎧", layout="centered")
st.title("🎧 Live Speech Translator")
st.caption("Speech -> STT -> Translation -> TTS")

ws_url = st.text_input("Backend WebSocket URL", value="ws://127.0.0.1:8000/ws")

uploaded = st.file_uploader(
    "Аудиофайл (WAV / MP3 / M4A / FLAC) — или оставь пустым для микрофона",
    type=["wav", "mp3", "m4a", "ogg", "webm", "flac"],
)
ref_uploaded = st.file_uploader("Референсное аудио для TTS (WAV)", type=["wav"])
ref_text_value = st.text_input("Референсная фраза для TTS", value="Это тестовая референсная фраза.")

audio_b64 = ""
audio_name = "микрофон"
if uploaded is not None:
    audio_b64 = base64.b64encode(uploaded.getvalue()).decode("ascii")
    audio_name = uploaded.name

ref_audio_b64 = ""
ref_audio_name = "тишина (по умолчанию)"
if ref_uploaded is not None:
    ref_audio_b64 = base64.b64encode(ref_uploaded.getvalue()).decode("ascii")
    ref_audio_name = ref_uploaded.name

components.html(
    f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Courier New', monospace;
    background: #0f0f0f;
    color: #e0e0e0;
    padding: 16px;
    min-height: 100vh;
  }}
  .badges {{
    display: flex;
    gap: 8px;
    margin-bottom: 14px;
    flex-wrap: wrap;
    align-items: center;
  }}
  .badge {{
    display: inline-block;
    padding: 3px 10px;
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
    border-radius: 4px;
    font-size: 11px;
    color: #555;
    letter-spacing: 0.04em;
  }}
  .controls {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 14px;
    flex-wrap: wrap;
  }}
  #btn {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 20px;
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 6px;
    color: #e0e0e0;
    cursor: pointer;
    font-family: inherit;
    font-size: 13px;
    letter-spacing: 0.05em;
    transition: background 0.15s, border-color 0.15s;
  }}
  #btn:hover  {{ background: #252525; border-color: #555; }}
  #btn.active {{ background: #2a0a0a; border-color: #8b1a1a; color: #ff6b6b; }}
  .dot {{
    width: 8px; height: 8px;
    border-radius: 50%;
    background: #444;
    transition: background 0.2s;
  }}
  #btn.active .dot {{
    background: #ff4444;
    box-shadow: 0 0 6px #ff4444;
    animation: pulse 1s infinite;
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50%       {{ opacity: 0.4; }}
  }}
  #status {{
    font-size: 12px;
    color: #555;
    letter-spacing: 0.04em;
  }}
  #status.live  {{ color: #4ade80; }}
  #status.error {{ color: #f87171; }}
  .transcript-wrap {{
    border: 1px solid #222;
    border-radius: 8px;
    background: #141414;
    padding: 14px 16px;
    min-height: 160px;
    margin-bottom: 10px;
    line-height: 1.8;
    font-size: 15px;
    letter-spacing: 0.01em;
    word-break: break-word;
    position: relative;
  }}
  .stable  {{ color: #e0e0e0; }}
  .pending {{
    color: #4a4a4a;
    font-style: italic;
    transition: color 0.25s;
  }}
  .pending.fresh {{ color: #6a6a6a; }}
  .cursor {{
    display: inline-block;
    width: 2px; height: 1em;
    background: #4ade80;
    margin-left: 2px;
    vertical-align: text-bottom;
    opacity: 0;
  }}
  .cursor.visible {{
    opacity: 1;
    animation: blink 1s step-end infinite;
  }}
  @keyframes blink {{
    0%, 100% {{ opacity: 1; }}
    50%       {{ opacity: 0; }}
  }}
  .latency-bar-wrap {{
    height: 3px;
    background: #1a1a1a;
    border-radius: 2px;
    margin-bottom: 10px;
    overflow: hidden;
  }}
  .latency-bar {{
    height: 100%;
    width: 0%;
    background: #4ade80;
    border-radius: 2px;
    transition: width 0.4s, background 0.4s;
  }}
  .stats {{
    display: flex;
    gap: 16px;
    font-size: 11px;
    color: #3a3a3a;
    letter-spacing: 0.05em;
    flex-wrap: wrap;
  }}
  .stat {{ display: flex; gap: 5px; }}
  .stat-val           {{ color: #555; }}
  .stat-val.fast      {{ color: #4ade80; }}
  .stat-val.ok        {{ color: #facc15; }}
  .stat-val.slow      {{ color: #f87171; }}
  #clear, #copy {{
    padding: 5px 12px;
    background: transparent;
    border: 1px solid #2a2a2a;
    border-radius: 5px;
    color: #444;
    cursor: pointer;
    font-family: inherit;
    font-size: 11px;
    letter-spacing: 0.05em;
    transition: border-color 0.15s, color 0.15s;
  }}
  #clear:hover {{ border-color: #555;    color: #888; }}
  #copy:hover  {{ border-color: #4ade80; color: #4ade80; }}
</style>
</head>
<body>
<div class="badges">
  <span class="badge">SOURCE: {audio_name}</span>
  <span class="badge">REF: {ref_audio_name}</span>
  <span class="badge" id="badge-lang">LANG: EN</span>
</div>
<div class="controls">
  <button id="btn" onclick="toggle()">
    <span class="dot"></span>
    <span id="btn-label">START</span>
  </button>
  <button id="clear" onclick="clearTranscript()">CLEAR</button>
  <button id="copy"  onclick="copyTranscript()">COPY</button>
  <span id="status">idle</span>
</div>
<div class="latency-bar-wrap">
  <div class="latency-bar" id="latency-bar"></div>
</div>
<div class="transcript-wrap" id="transcript-wrap">
  <span class="stable"  id="stable"></span>
  <span class="pending" id="pending"></span>
  <span class="cursor"  id="cursor"></span>
</div>
<div class="stats">
  <div class="stat">CHARS   <span class="stat-val" id="stat-chars">0</span></div>
  <div class="stat">WORDS   <span class="stat-val" id="stat-words">0</span></div>
  <div class="stat">MODEL   <span class="stat-val" id="stat-lat">—</span></div>
  <div class="stat">AVG     <span class="stat-val" id="stat-avg">—</span></div>
  <div class="stat"><span class="stat-val" id="stat-log"></span></div>
</div>
<script>
const WS_URL = {json.dumps(ws_url)};
const HAS_FILE = {str(bool(audio_b64)).lower()};
const AUDIO_B64 = {json.dumps(audio_b64)};
const REF_AUDIO_B64 = {json.dumps(ref_audio_b64)};
const REF_TEXT = {json.dumps(ref_text_value)};

const SAMPLE_RATE = 16000;
const CHUNK_SEC = 0.1;
const TTS_DEFAULT_SR = 24000;

let ws = null;
let recording = false;
let inputCtx = null;
let micStream = null;
let micSrc = null;
let processor = null;
let inputBuf = [];
let pendingTimer = null;

let playbackCtx = null;
let playbackSampleRate = TTS_DEFAULT_SR;
let nextPlaybackTime = 0;

function setStatus(text, cls = "") {{
  const el = document.getElementById("status");
  el.textContent = text;
  el.className = cls;
}}

function setBtn(active) {{
  document.getElementById("btn").className = active ? "active" : "";
  document.getElementById("btn-label").textContent = active ? "STOP" : "START";
}}

function setCursor(visible) {{
  document.getElementById("cursor").className = "cursor" + (visible ? " visible" : "");
}}

function setTranscript(stable, pending) {{
  document.getElementById("stable").textContent = stable || "";
  const pEl = document.getElementById("pending");
  pEl.textContent = pending ? (" " + pending) : "";
  pEl.classList.add("fresh");
  clearTimeout(pendingTimer);
  pendingTimer = setTimeout(() => pEl.classList.remove("fresh"), 350);
}}

function updateStats(stable, pending, log) {{
  const full = ((stable || "") + " " + (pending || "")).trim();
  document.getElementById("stat-chars").textContent = (stable || "").length;
  document.getElementById("stat-words").textContent = full ? full.split(/\\s+/).filter(Boolean).length : 0;
  document.getElementById("stat-log").textContent = log || "";

  const mModel = log && log.match(/model\\s+(\\d+)ms/);
  const mAvg = log && log.match(/avg\\s+(\\d+)ms/);
  const modelMs = mModel ? parseInt(mModel[1], 10) : null;
  const avgMs = mAvg ? parseInt(mAvg[1], 10) : null;

  if (modelMs !== null) {{
    const latEl = document.getElementById("stat-lat");
    const barEl = document.getElementById("latency-bar");
    latEl.textContent = modelMs + "ms";
    latEl.className = modelMs < 800 ? "stat-val fast" : (modelMs < 1500 ? "stat-val ok" : "stat-val slow");
    const pct = Math.min((modelMs / 2000) * 100, 100);
    barEl.style.width = pct + "%";
    barEl.style.background = modelMs < 800 ? "#4ade80" : (modelMs < 1500 ? "#facc15" : "#f87171");
  }}

  if (avgMs !== null) {{
    const avgEl = document.getElementById("stat-avg");
    avgEl.textContent = avgMs + "ms";
    avgEl.className = avgMs < 800 ? "stat-val fast" : (avgMs < 1500 ? "stat-val ok" : "stat-val slow");
  }}
}}

function clearTranscript() {{
  setTranscript("", "");
  updateStats("", "", "");
}}

function copyTranscript() {{
  const text = (document.getElementById("stable").textContent || "").trim();
  if (!text) return;
  navigator.clipboard.writeText(text).catch(() => {{}});
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
  for (let i = 0; i < int16.length; i++) out[i] = int16[i] / 32768;
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

function b64ToBytes(b64) {{
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr;
}}

async function streamFile() {{
  setStatus("decoding…", "");
  const bytes = b64ToBytes(AUDIO_B64);
  const decCtx = new AudioContext();
  const decoded = await decCtx.decodeAudioData(bytes.buffer.slice(0));
  const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * SAMPLE_RATE), SAMPLE_RATE);
  const src = offline.createBufferSource();
  src.buffer = decoded;
  src.connect(offline.destination);
  src.start();
  const rendered = await offline.startRendering();
  const samples = rendered.getChannelData(0);
  setStatus("streaming…", "live");
  const chunkN = Math.floor(SAMPLE_RATE * CHUNK_SEC);
  for (let i = 0; i < samples.length && recording; i += chunkN) {{
    const chunk = new Float32Array(samples.subarray(i, Math.min(i + chunkN, samples.length)));
    if (ws?.readyState === WebSocket.OPEN) ws.send(chunk.buffer);
    await new Promise((r) => setTimeout(r, CHUNK_SEC * 1000));
  }}
  if (recording) {{
    ws.send(JSON.stringify({{ event: "session_end" }}));
    setStatus("sent, processing…", "live");
  }}
}}

async function startMic() {{
  micStream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
  inputCtx = new AudioContext({{ sampleRate: SAMPLE_RATE }});
  micSrc = inputCtx.createMediaStreamSource(micStream);
  processor = inputCtx.createScriptProcessor(4096, 1, 1);
  const chunkN = Math.floor(SAMPLE_RATE * CHUNK_SEC);
  processor.onaudioprocess = (e) => {{
    inputBuf.push(...e.inputBuffer.getChannelData(0));
    while (inputBuf.length >= chunkN) {{
      const chunk = new Float32Array(inputBuf.splice(0, chunkN));
      if (ws?.readyState === WebSocket.OPEN) ws.send(chunk.buffer);
    }}
  }};
  micSrc.connect(processor);
  processor.connect(inputCtx.destination);
  setStatus("streaming…", "live");
}}

function toggle() {{ recording ? stop() : start(); }}

async function start() {{
  recording = true;
  setBtn(true);
  setCursor(true);
  setStatus("connecting…", "");
  clearTranscript();
  await ensurePlaybackContext();

  ws = new WebSocket(WS_URL);
  ws.binaryType = "arraybuffer";

  ws.onopen = async () => {{
    ws.send(JSON.stringify({{
      event: "session_start",
      ref_audio: REF_AUDIO_B64 || "",
      ref_text: REF_TEXT || "",
      lang: "Russian",
    }}));
    if (HAS_FILE) await streamFile();
    else await startMic();
  }};

  ws.onmessage = async (e) => {{
    if (typeof e.data !== "string") {{
      await schedulePcmFrame(e.data);
      return;
    }}
    let p = null;
    try {{ p = JSON.parse(e.data); }} catch {{ return; }}

    if ((p.event === "translation" || p.stable !== undefined) && p.stable !== undefined) {{
      setTranscript(p.stable, p.pending || "");
      updateStats(p.stable, p.pending, p.speed_logs);
      return;
    }}
    if (p.event === "started" && p.sample_rate) {{
      playbackSampleRate = Number(p.sample_rate) || TTS_DEFAULT_SR;
      return;
    }}
    if (p.event === "error") {{
      setStatus("error", "error");
      return;
    }}
    if (p.event === "done") {{
      setStatus("done", "");
    }}
  }};

  ws.onerror = () => setStatus("websocket error", "error");
  ws.onclose = () => {{ if (recording) setStatus("connection closed", "error"); }};
}}

function stop() {{
  recording = false;
  setBtn(false);
  setCursor(false);
  setStatus("stopped", "");
  inputBuf = [];

  clearTimeout(pendingTimer);
  if (processor) processor.disconnect();
  if (micSrc) micSrc.disconnect();
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  if (inputCtx) inputCtx.close().catch(() => {{}});
  processor = null;
  micSrc = null;
  micStream = null;
  inputCtx = null;

  if (ws?.readyState === WebSocket.OPEN) {{
    ws.send(JSON.stringify({{ event: "session_end" }}));
  }}
  if (ws) ws.close();
  ws = null;
}}
</script>
</body>
</html>
""",
    height=560,
)
