"""
Streamlit TTS Frontend
=======================
pip install streamlit
streamlit run app.py
"""

import base64
import json
import streamlit as st
import streamlit.components.v1 as components

# ─── Конфиг страницы ──────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Voice Synth",
    page_icon="🎙",
    layout="centered",
)

# ─── CSS ──────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@300;400;500&display=swap');

:root {
    --bg:        #0c0c0f;
    --surface:   #13131a;
    --border:    #1e1e2e;
    --accent:    #7c6af7;
    --accent2:   #c084fc;
    --text:      #e2e0f0;
    --muted:     #5a5875;
    --green:     #4ade80;
    --red:       #f87171;
}

html, body, [data-testid="stAppViewContainer"] {
    background: var(--bg) !important;
    color: var(--text) !important;
    font-family: 'DM Mono', monospace !important;
}

[data-testid="stHeader"] { background: transparent !important; }
[data-testid="stToolbar"] { display: none !important; }

/* Заголовок */
.hero {
    text-align: center;
    padding: 2.5rem 0 2rem;
    position: relative;
}
.hero-title {
    font-family: 'Syne', sans-serif;
    font-size: 3rem;
    font-weight: 800;
    letter-spacing: -0.03em;
    background: linear-gradient(135deg, #fff 0%, var(--accent2) 60%, var(--accent) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    line-height: 1.1;
    margin: 0;
}
.hero-sub {
    font-size: 0.8rem;
    color: var(--muted);
    letter-spacing: 0.15em;
    text-transform: uppercase;
    margin-top: 0.5rem;
}

/* Карточки секций */
.section-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.4rem 1.6rem;
    margin-bottom: 1rem;
}
.section-label {
    font-family: 'Syne', sans-serif;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 0.7rem;
}

/* Streamlit виджеты */
[data-testid="stFileUploader"] > div {
    background: var(--bg) !important;
    border: 1px dashed var(--border) !important;
    border-radius: 8px !important;
}
[data-testid="stFileUploader"] label,
[data-testid="stTextInput"] label,
[data-testid="stTextArea"] label,
[data-testid="stSelectbox"] label {
    font-family: 'Syne', sans-serif !important;
    font-size: 0.72rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.12em !important;
    text-transform: uppercase !important;
    color: var(--muted) !important;
}
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea {
    background: var(--bg) !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    color: var(--text) !important;
    font-family: 'DM Mono', monospace !important;
    font-size: 0.85rem !important;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 2px rgba(124,106,247,0.2) !important;
}
[data-testid="stSelectbox"] > div > div {
    background: var(--bg) !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    color: var(--text) !important;
}

/* Кнопка */
[data-testid="stButton"] button {
    width: 100% !important;
    background: linear-gradient(135deg, var(--accent), var(--accent2)) !important;
    color: #fff !important;
    border: none !important;
    border-radius: 10px !important;
    font-family: 'Syne', sans-serif !important;
    font-size: 0.9rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.08em !important;
    padding: 0.8rem 1.5rem !important;
    cursor: pointer !important;
    transition: opacity 0.2s, transform 0.1s !important;
}
[data-testid="stButton"] button:hover {
    opacity: 0.88 !important;
    transform: translateY(-1px) !important;
}
[data-testid="stButton"] button:active {
    transform: translateY(0) !important;
}

/* Статус */
.status-bar {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.7rem 1rem;
    border-radius: 8px;
    font-size: 0.82rem;
    margin-bottom: 1rem;
    font-family: 'DM Mono', monospace;
}
.status-idle    { background: rgba(255,255,255,0.04); border: 1px solid var(--border); color: var(--muted); }
.status-playing { background: rgba(74,222,128,0.08);  border: 1px solid rgba(74,222,128,0.3); color: var(--green); }
.status-error   { background: rgba(248,113,113,0.08); border: 1px solid rgba(248,113,113,0.3); color: var(--red); }
.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.dot-idle    { background: var(--muted); }
.dot-playing { background: var(--green); animation: pulse 1s infinite; }
.dot-error   { background: var(--red); }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

/* Убираем лишнее от Streamlit */
#MainMenu, footer { visibility: hidden; }
.block-container { max-width: 720px !important; padding-top: 0 !important; }
</style>
""", unsafe_allow_html=True)


# ─── Заголовок ────────────────────────────────────────────────────────────────

st.markdown("""
<div class="hero">
    <h1 class="hero-title">Voice Synth</h1>
    <p class="hero-sub">Streaming TTS · Real-time audio</p>
</div>
""", unsafe_allow_html=True)


# ─── Состояние ────────────────────────────────────────────────────────────────

if "ws_url" not in st.session_state:
    st.session_state.ws_url = "ws://localhost:8000/api/generate"


# ─── Настройки подключения ────────────────────────────────────────────────────

with st.expander("⚙ Настройки сервера", expanded=False):
    st.session_state.ws_url = st.text_input(
        "WebSocket URL",
        value=st.session_state.ws_url,
        placeholder="ws://localhost:8000/api/generate",
    )


# ─── Голосовой промпт ─────────────────────────────────────────────────────────

st.markdown('<div class="section-card">', unsafe_allow_html=True)
st.markdown('<div class="section-label">🎤 Референсный голос</div>', unsafe_allow_html=True)

col1, col2 = st.columns([1, 1])
with col1:
    ref_audio_file = st.file_uploader(
        "Аудио файл (WAV)",
        type=["wav", "mp3", "ogg", "flac"],
        label_visibility="visible",
    )
with col2:
    lang = st.selectbox(
        "Язык синтеза",
        ["Russian", "English", "Chinese", "Japanese", "Korean",
         "German", "French", "Spanish", "Italian", "Portuguese"],
        index=0,
    )

ref_text = st.text_input(
    "Транскрипция референсного аудио",
    placeholder="Текст, произнесённый в аудио файле...",
    max_chars=500,
)
st.markdown('</div>', unsafe_allow_html=True)


# ─── Текст для синтеза ────────────────────────────────────────────────────────

st.markdown('<div class="section-card">', unsafe_allow_html=True)
st.markdown('<div class="section-label">✍ Текст для синтеза</div>', unsafe_allow_html=True)

text_input = st.text_area(
    "Введите текст",
    placeholder="Мороз и солнце; день чудесный!\nЕще ты дремлешь, друг прелестный...",
    height=160,
    label_visibility="collapsed",
)
st.markdown('</div>', unsafe_allow_html=True)


# ─── Кнопка и плеер ──────────────────────────────────────────────────────────

generate_clicked = st.button("▶  Синтезировать и воспроизвести")

# Область для WebSocket-компонента (плеер + статус)
player_placeholder = st.empty()


# ─── Логика запуска ───────────────────────────────────────────────────────────

def build_player_html(ws_url: str, ref_audio_b64: str, ref_text: str,
                      lang: str, text: str) -> str:
    """
    Генерирует HTML+JS компонент, который:
      1. Подключается к WebSocket
      2. Получает бинарные PCM-чанки (4-байт header + int16 PCM 24kHz)
      3. Воспроизводит их через Web Audio API без задержек
      4. Показывает статус и визуализацию
    """

    payload = json.dumps({
        "ref_audio": ref_audio_b64,
        "ref_text":  ref_text,
        "lang":      lang,
        "text":      text,
    })

    return f"""
<!DOCTYPE html>
<html>
<head>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    font-family: 'DM Mono', 'Courier New', monospace;
    background: transparent;
    color: #e2e0f0;
    padding: 4px 0;
  }}

  .player-wrap {{
    background: #13131a;
    border: 1px solid #1e1e2e;
    border-radius: 12px;
    overflow: hidden;
  }}

  /* Визуализация */
  canvas {{
    width: 100%;
    height: 56px;
    display: block;
    background: #0c0c0f;
  }}

  /* Статусная строка */
  .status {{
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 16px;
    font-size: 12px;
    border-top: 1px solid #1e1e2e;
  }}
  .dot {{
    width: 7px; height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
    transition: background 0.3s;
  }}
  .dot.idle    {{ background: #5a5875; }}
  .dot.loading {{ background: #fbbf24; animation: pulse 0.8s infinite; }}
  .dot.playing {{ background: #4ade80; animation: pulse 1s infinite; }}
  .dot.done    {{ background: #7c6af7; }}
  .dot.error   {{ background: #f87171; }}
  @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:0.35}} }}

  #msg {{ color: #a09ec0; flex: 1; }}

  .stats {{
    margin-left: auto;
    color: #3d3b55;
    font-size: 11px;
    letter-spacing: 0.05em;
  }}

  /* Прогресс-бар */
  .progress-wrap {{
    height: 2px;
    background: #1e1e2e;
  }}
  .progress-bar {{
    height: 100%;
    background: linear-gradient(90deg, #7c6af7, #c084fc);
    width: 0%;
    transition: width 0.4s ease;
  }}
</style>
</head>
<body>
<div class="player-wrap">
  <canvas id="viz" width="700" height="56"></canvas>
  <div class="progress-wrap">
    <div class="progress-bar" id="progress"></div>
  </div>
  <div class="status">
    <span class="dot loading" id="dot"></span>
    <span id="msg">Подключение к серверу...</span>
    <span class="stats" id="stats"></span>
  </div>
</div>

<script>
(function() {{
  const WS_URL   = {json.dumps(ws_url)};
  const PAYLOAD  = {payload};
  const SR       = 24000;

  // DOM
  const dot      = document.getElementById('dot');
  const msg      = document.getElementById('msg');
  const stats    = document.getElementById('stats');
  const progress = document.getElementById('progress');
  const canvas   = document.getElementById('viz');
  const ctx      = canvas.getContext('2d');

  // Состояние
  let audioCtx       = null;
  let nextPlayAt     = 0;       // когда начать следующий чанк
  let chunksReceived = 0;
  let totalSamples   = 0;
  let startTime      = null;
  let vizBuf         = new Float32Array(700);  // для визуализации

  // ── Статус ──────────────────────────────────────────────────────────────

  function setStatus(state, text) {{
    dot.className = 'dot ' + state;
    msg.textContent = text;
  }}

  // ── Web Audio ─────────────────────────────────────────────────────────────

  function initAudio() {{
    audioCtx  = new (window.AudioContext || window.webkitAudioContext)({{ sampleRate: SR }});
    nextPlayAt = audioCtx.currentTime + 0.05;  // 50мс стартовый буфер
  }}

  function playPCM(int16Array) {{
    if (!audioCtx) return;

    const n = int16Array.length;
    const f32 = new Float32Array(n);
    for (let i = 0; i < n; i++) f32[i] = int16Array[i] / 32768.0;

    const buf = audioCtx.createBuffer(1, n, SR);
    buf.getChannelData(0).set(f32);

    const src = audioCtx.createBufferSource();
    src.buffer = buf;
    src.connect(audioCtx.destination);

    const now = audioCtx.currentTime;
    const when = Math.max(nextPlayAt, now);
    src.start(when);
    nextPlayAt = when + buf.duration;

    // Обновляем буфер визуализации (rolling)
    const chunk = f32.slice(0, Math.min(f32.length, 350));
    vizBuf = new Float32Array([...vizBuf.slice(chunk.length), ...chunk]);
    drawViz();

    totalSamples += n;
  }}

  // ── Визуализация ──────────────────────────────────────────────────────────

  function drawViz() {{
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    // Фон
    ctx.fillStyle = '#0c0c0f';
    ctx.fillRect(0, 0, W, H);

    // Центральная линия
    ctx.strokeStyle = '#1e1e2e';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, H/2);
    ctx.lineTo(W, H/2);
    ctx.stroke();

    // Waveform
    const grad = ctx.createLinearGradient(0, 0, W, 0);
    grad.addColorStop(0,   'rgba(124,106,247,0.6)');
    grad.addColorStop(0.5, 'rgba(192,132,252,0.9)');
    grad.addColorStop(1,   'rgba(124,106,247,0.6)');
    ctx.strokeStyle = grad;
    ctx.lineWidth = 1.5;
    ctx.beginPath();

    const step = W / vizBuf.length;
    for (let i = 0; i < vizBuf.length; i++) {{
      const x = i * step;
      const y = H/2 + vizBuf[i] * (H/2 - 4);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }}
    ctx.stroke();

    // Заполнение под кривой
    ctx.lineTo(W, H/2);
    ctx.lineTo(0, H/2);
    ctx.closePath();
    const fill = ctx.createLinearGradient(0, 0, 0, H);
    fill.addColorStop(0, 'rgba(124,106,247,0.12)');
    fill.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = fill;
    ctx.fill();
  }}

  // Idle анимация
  function drawIdle() {{
    const W = canvas.width, H = canvas.height;
    ctx.fillStyle = '#0c0c0f';
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = '#1e1e2e';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, H/2); ctx.lineTo(W, H/2); ctx.stroke();

    // Пульс
    const t = Date.now() / 1000;
    ctx.strokeStyle = 'rgba(124,106,247,0.25)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (let x = 0; x < W; x++) {{
      const y = H/2 + Math.sin(x * 0.04 + t * 2) * 3;
      x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }}
    ctx.stroke();
  }}

  let idleAnim = setInterval(drawIdle, 50);

  // ── WebSocket ─────────────────────────────────────────────────────────────

  startTime = Date.now();
  initAudio();

  const ws = new WebSocket(WS_URL);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {{
    setStatus('loading', 'Синтез речи...');
    ws.send(JSON.stringify(PAYLOAD));
  }};

  ws.onmessage = (e) => {{
    if (typeof e.data === 'string') {{
      // JSON событие
      const ev = JSON.parse(e.data);
      if (ev.event === 'done') {{
        clearInterval(idleAnim);
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
        const dur     = (totalSamples / SR).toFixed(1);
        setStatus('done', `Готово — ${{dur}}с аудио`);
        stats.textContent = `${{ev.chunks}} чанков · ${{elapsed}}с`;
        progress.style.width = '100%';
      }}
      if (ev.event === 'error') {{
        clearInterval(idleAnim);
        setStatus('error', 'Ошибка: ' + ev.message);
      }}
      return;
    }}

    // Бинарный чанк: [uint32 len][pcm int16]
    const view = new DataView(e.data);
    const pcmLen = view.getUint32(0, true);
    const int16  = new Int16Array(e.data, 4, pcmLen / 2);

    clearInterval(idleAnim);
    idleAnim = null;

    if (chunksReceived === 0) {{
      setStatus('playing', 'Воспроизведение...');
    }}
    chunksReceived++;

    playPCM(int16);
    stats.textContent = `чанк ${{chunksReceived}}`;

    // Прогресс (эвристика)
    const p = Math.min(chunksReceived * 18, 92);
    progress.style.width = p + '%';
  }};

  ws.onerror = () => {{
    clearInterval(idleAnim);
    setStatus('error', 'Ошибка подключения к ' + WS_URL);
  }};

  ws.onclose = (e) => {{
    if (e.code !== 1000 && chunksReceived === 0) {{
      clearInterval(idleAnim);
      setStatus('error', 'Сервер недоступен');
    }}
  }};

}})();
</script>
</body>
</html>
"""


# ─── Обработка нажатия ────────────────────────────────────────────────────────

if generate_clicked:
    errors = []
    if not ref_audio_file:
        errors.append("Загрузите аудио файл референсного голоса")
    if not text_input.strip():
        errors.append("Введите текст для синтеза")

    if errors:
        for e in errors:
            st.error(e)
    else:
        # Кодируем аудио в base64
        audio_bytes   = ref_audio_file.read()
        audio_b64     = base64.b64encode(audio_bytes).decode()

        html = build_player_html(
            ws_url       = st.session_state.ws_url,
            ref_audio_b64= audio_b64,
            ref_text     = ref_text,
            lang         = lang,
            text         = text_input.strip(),
        )

        with player_placeholder:
            components.html(html, height=120, scrolling=False)