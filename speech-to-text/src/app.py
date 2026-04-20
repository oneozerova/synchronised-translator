import base64
import json
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="STT", page_icon="🎙️")
st.title("🎙️ Speech → Text")
st.caption("Говорите по-английски или загрузите аудио")

WS_URL = "wss://apollo2.ci.nsu.ru/m.unzhakov/proxy/8010/ws"
# WS_URL = "ws://127.0.0.1:8010/ws"

uploaded = st.file_uploader(
    "Загрузите аудио",
    type=["wav", "mp3", "m4a", "ogg", "webm", "flac"],
)

audio_b64 = None
audio_mime = ""
audio_name = ""

if uploaded is not None:
    audio_bytes = uploaded.getvalue()
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
    audio_mime = uploaded.type
    audio_name = uploaded.name

components.html(f"""
<style>
  body {{ font-family: sans-serif; margin: 0; }}
  #btn {{
    padding: 12px 28px;
    font-size: 16px;
    cursor: pointer;
    border-radius: 8px;
    border: none;
    background: #f63366;
    color: white;
  }}
  #btn.active {{ background: #555; }}
  #status {{
    margin: 8px 0;
    color: #888;
    font-size: 13px;
  }}
  #output {{
    margin-top: 12px;
    padding: 16px;
    background: #f8f8f8;
    border-radius: 8px;
    min-height: 140px;
    font-size: 16px;
    line-height: 1.7;
    border: 1px solid #e0e0e0;
  }}
</style>

<button id="btn" onclick="toggle()">🎙️ Start</button>
<div id="status">Idle</div>

<div style="margin-top:10px; font-size:13px; color:#666;">
  {"📁 " + audio_name if audio_name else "📁 файл не выбран (используется микрофон)"}
</div>

<audio id="player" controls style="width:100%; margin-top:10px;"></audio>

<div id="output"><span id="committed"></span></div>

<script>
const WS_URL   = "{WS_URL}";
const HAS_FILE = {str(audio_b64 is not None).lower()};
const AUDIO_B64  = {json.dumps(audio_b64 or "")};
const AUDIO_MIME = {json.dumps(audio_mime)};

const SAMPLE_RATE = 16000;
const CHUNK_SEC   = 0.25;

let ws, audioCtx, processor, source, stream;
let recording = false;
let buffer = [];

function toggle() {{
  recording ? stop() : start();
}}

async function start() {{
  recording = true;
  setBtn(true);
  setStatus("Connecting...");

  document.getElementById("output").innerHTML = '<span id="committed"></span>';

  ws = new WebSocket(WS_URL);
  ws.binaryType = "arraybuffer";

  ws.onopen = async () => {{
    if (HAS_FILE) {{
      setStatus("Playing + streaming file");

      // ▶️ ВОСПРОИЗВЕДЕНИЕ
      const player = document.getElementById("player");
      player.src = "data:" + AUDIO_MIME + ";base64," + AUDIO_B64;
      player.currentTime = 0;

      try {{
        await player.play();
      }} catch (e) {{
        console.log("Playback blocked:", e);
      }}

      // ▶️ СТРИМИНГ
      await streamFile();
    }} else {{
      setStatus("Recording mic");

      stream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
      audioCtx = new AudioContext({{ sampleRate: SAMPLE_RATE }});
      source = audioCtx.createMediaStreamSource(stream);
      processor = audioCtx.createScriptProcessor(4096, 1, 1);

      const chunkSamples = Math.floor(SAMPLE_RATE * CHUNK_SEC);

      processor.onaudioprocess = (e) => {{
        buffer.push(...e.inputBuffer.getChannelData(0));

        while (buffer.length >= chunkSamples) {{
          const chunk = new Float32Array(buffer.splice(0, chunkSamples));
          if (ws.readyState === WebSocket.OPEN) {{
            ws.send(chunk.buffer);
          }}
        }}
      }};

      source.connect(processor);
      processor.connect(audioCtx.destination);
    }}
  }};

  ws.onmessage = (e) => {{
    let payload;
    try {{
      payload = JSON.parse(e.data);
    }} catch {{
      return;
    }}

    const committedEl = document.getElementById("committed");
    if (committedEl) {{
      committedEl.textContent = payload.stable ?? "";
    }}
  }};
}}

async function streamFile() {{
  const bytes = base64ToUint8Array(AUDIO_B64);
  const arrayBuffer = bytes.buffer;

  const ctx = new AudioContext();
  const decoded = await ctx.decodeAudioData(arrayBuffer.slice(0));

  // ✅ РЕСЕМПЛ В 16kHz (КРИТИЧНО)
  const offline = new OfflineAudioContext(
    1,
    Math.ceil(decoded.duration * 16000),
    16000
  );

  const src = offline.createBufferSource();
  src.buffer = decoded;
  src.connect(offline.destination);
  src.start();

  const rendered = await offline.startRendering();
  const samples = rendered.getChannelData(0);

  const chunkSamples = Math.floor(16000 * CHUNK_SEC);

  for (let i = 0; i < samples.length && recording; i += chunkSamples) {{
    const end = Math.min(i + chunkSamples, samples.length);
    const chunk = new Float32Array(samples.subarray(i, end));

    if (ws.readyState === WebSocket.OPEN) {{
      ws.send(chunk.buffer);
    }}

    await sleep(CHUNK_SEC * 1000);
  }}
}}

function stop() {{
  recording = false;
  setBtn(false);
  setStatus("Stopped");

  if (processor) processor.disconnect();
  if (source) source.disconnect();
  if (stream) stream.getTracks().forEach(t => t.stop());
  if (ws) ws.close();

  const player = document.getElementById("player");
  if (player) {{
    player.pause();
    player.currentTime = 0;
  }}

  buffer = [];
}}

function base64ToUint8Array(base64) {{
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {{
    bytes[i] = binary.charCodeAt(i);
  }}
  return bytes;
}}

function sleep(ms) {{
  return new Promise(r => setTimeout(r, ms));
}}

function setBtn(active) {{
  const b = document.getElementById("btn");
  b.textContent = active ? "⏹️ Stop" : "🎙️ Start";
  b.className = active ? "active" : "";
}}

function setStatus(msg) {{
  document.getElementById("status").textContent = msg;
}}
</script>
""", height=500)