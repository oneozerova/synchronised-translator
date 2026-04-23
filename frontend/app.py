"""
Единый Streamlit frontend:
- отправка аудио (микрофон / файл) в backend websocket
- отображение streaming перевода
- воспроизведение streaming TTS
- поддержка reference audio / reference text для TTS
"""

import base64
import html
import json

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Speech Translator", page_icon="🎙️", layout="wide")

st.markdown(
    """
<style>
  .stApp {
    background: #FDB492;
    color: #000000;
  }

  .block-container {
    max-width: 1180px;
    padding-top: 2rem;
    padding-bottom: 2.2rem;
  }

  h1, h2, h3 {
    font-family: "Avenir Next", "Trebuchet MS", sans-serif;
    letter-spacing: -0.02em;
    color: #000000;
  }

  p, div, span, label {
    color: #000000;
  }

  .hero-shell {
    border: 1px solid rgba(0, 0, 0, 0.12);
    border-radius: 28px;
    padding: 28px 30px;
    margin-bottom: 18px;
    background: rgba(255, 244, 237, 0.58);
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.10);
  }

  .hero-title {
    margin: 0 0 10px 0;
    font-size: 2.5rem;
    line-height: 1;
    color: #000000;
  }

  .hero-subtitle {
    margin: 0;
    max-width: 760px;
    font-size: 1.06rem;
    line-height: 1.55;
    color: #000000;
  }

  .tip-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
    margin-top: 18px;
  }

  .tip-card {
    border-radius: 18px;
    padding: 14px 16px;
    background: rgba(255, 248, 242, 0.72);
    border: 1px solid rgba(0, 0, 0, 0.10);
  }

  .tip-title {
    display: block;
    margin-bottom: 4px;
    font-size: 0.82rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #000000;
  }

  .tip-text {
    font-size: 0.98rem;
    line-height: 1.4;
    color: #000000;
  }

  .section-note {
    margin: 10px 0 18px 0;
    color: #000000;
    font-size: 0.96rem;
  }

  /* input wrappers */
  div[data-baseweb="input"] > div,
  div[data-baseweb="base-input"] > div,
  .stTextInput > div > div,
  .stFileUploader,
  section[data-testid="stFileUploaderDropzone"] {
    background: rgba(255, 248, 242, 0.72) !important;
    border: 1px solid rgba(0, 0, 0, 0.12) !important;
    color: #000000 !important;
    border-radius: 16px !important;
  }

  input, textarea {
    color: #000000 !important;
    background: transparent !important;
    -webkit-text-fill-color: #000000 !important;
  }

  input::placeholder,
  textarea::placeholder {
    color: #000000 !important;
  }

  label, .stMarkdown, .stTextInput label {
    color: #000000 !important;
  }

  .stFileUploader label,
  .stFileUploader label span,
  .stFileUploader [data-testid="stWidgetLabel"],
  .stFileUploader [data-testid="stWidgetLabel"] *,
  [data-testid="stFileUploader"] label,
  [data-testid="stFileUploader"] label * {
    color: #000000 !important;
  }

  .stFileUploader button,
  .stFileUploader button span,
  [data-testid="stFileUploader"] button,
  [data-testid="stFileUploader"] button span,
  [data-testid="stBaseButton-secondary"],
  [data-testid="stBaseButton-secondary"] * {
    color: #000000 !important;
  }

  /* expander */
  [data-testid="stExpander"] details {
    background: rgba(255, 244, 237, 0.58);
    border-radius: 18px;
    border: 1px solid rgba(0, 0, 0, 0.10);
    overflow: hidden;
  }

  [data-testid="stExpander"] summary {
    color: #000000 !important;
  }

  [data-testid="stExpander"] details > div {
    background: transparent !important;
  }

  /* helper text / uploader text */
  .stFileUploader small,
  .stTextInput small,
  [data-testid="stFileUploaderDropzoneInstructions"],
  [data-testid="stFileUploaderDropzone"] small,
  [data-testid="stFileUploaderDropzoneInstructions"] *,
  [data-testid="stFileUploaderDropzone"] small * {
    color: #000000 !important;
  }

  /* remove white headers/containers if theme leaks */
  [data-testid="stHeader"] {
    background: transparent !important;
  }

  @media (max-width: 900px) {
    .tip-grid {
      grid-template-columns: 1fr;
    }
    .hero-title {
      font-size: 2rem;
    }
  }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<section class="hero-shell">
  <h1 class="hero-title">Синхронный перевод речи</h1>
  <p class="hero-subtitle">
    Говорите спокойно и немного медленнее обычного. Интерфейс показывает текст в реальном времени,
    а голосовая строка помогает понять, что система сейчас слушает или озвучивает ответ.
  </p>
  <div class="tip-grid">
    <div class="tip-card">
      <span class="tip-title">Как говорить</span>
      <div class="tip-text">Короткие фразы, чёткая дикция, небольшая пауза между предложениями.</div>
    </div>
    <div class="tip-card">
      <span class="tip-title">Если шумно</span>
      <div class="tip-text">Поднесите микрофон ближе или загрузите готовый аудиофайл вместо записи.</div>
    </div>
    <div class="tip-card">
      <span class="tip-title">Что увидите</span>
      <div class="tip-text">Нижний блок показывает подтверждённый текст, текущий хвост и скорость модели.</div>
    </div>
  </div>
</section>
""",
    unsafe_allow_html=True,
)

controls_left, controls_right = st.columns([1.15, 0.85], gap="large")
with controls_left:
    ws_url = st.text_input("Backend WebSocket URL", value="ws://127.0.0.1:8000/ws")
with controls_right:
    ref_text_value = st.text_input(
        "Референсная фраза для TTS",
        value="Это тестовая референсная фраза.",
    )

with st.expander("Источник аудио и настройки голоса", expanded=True):
    uploads_left, uploads_right = st.columns(2, gap="large")
    with uploads_left:
        uploaded = st.file_uploader(
            "Аудиофайл вместо микрофона",
            type=["wav", "mp3", "m4a", "ogg", "webm", "flac"],
            help="Если файл не выбран, интерфейс будет слушать микрофон.",
        )
    with uploads_right:
        ref_uploaded = st.file_uploader(
            "Референсное аудио для TTS",
            type=["wav"],
            help="Необязательно. Если не выбрать, будет использован голос по умолчанию.",
        )

st.markdown(
    """
<div class="section-note">
  Нажмите кнопку запуска ниже и начните говорить. Текст остаётся основным результатом,
  озвучка и анимация работают поверх той же логики websocket.
</div>
""",
    unsafe_allow_html=True,
)

audio_b64 = ""
audio_name = "Микрофон"
if uploaded is not None:
    audio_b64 = base64.b64encode(uploaded.getvalue()).decode("ascii")
    audio_name = uploaded.name

ref_audio_b64 = ""
ref_audio_name = "Голос по умолчанию"
if ref_uploaded is not None:
    ref_audio_b64 = base64.b64encode(ref_uploaded.getvalue()).decode("ascii")
    ref_audio_name = ref_uploaded.name

audio_label = html.escape(audio_name)
ref_audio_label = html.escape(ref_audio_name)
wave_bars_html = "".join('<span class="voice-bar"></span>' for _ in range(48))

components.html(
    f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body {{
    height: 100%;
    overflow-y: auto;
  }}
  :root {{
    --bg: #FDB492;
    --panel: rgba(255, 244, 237, 0.78);
    --panel-soft: rgba(255, 239, 230, 0.88);
    --line: rgba(0, 0, 0, 0.12);
    --text: #000000;
    --muted: #000000;
    --accent: #799DFF;
    --accent-soft: rgba(121, 157, 255, 0.18);
    --active: #799DFF;
    --error: #b94a48;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    min-height: 100vh;
    padding: 0;
    background: transparent;
    color: var(--text);
    font-family: "Avenir Next", "Trebuchet MS", sans-serif;
  }}
  .shell {{
    padding: 0;
    background: transparent;
    min-height: auto;
  }}
  .panel {{
    border-radius: 24px;
    background: var(--panel);
    border: 1px solid var(--line);
    box-shadow: 0 16px 36px rgba(0, 0, 0, 0.10);
    overflow: hidden;
    min-height: auto;
  }}
  .hero {{
    padding: 24px 24px 18px;
    background: rgba(255, 248, 242, 0.72);
    border-bottom: 1px solid var(--line);
    display: flex;
    flex-direction: column;
  }}
  .topline {{
    display: flex;
    justify-content: space-between;
    gap: 14px;
    flex-wrap: wrap;
    margin-bottom: 14px;
  }}
  .badge-row {{
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }}
  .badge {{
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 8px 12px;
    border-radius: 999px;
    border: 1px solid rgba(0,0,0,0.12);
    background: rgba(255,255,255,0.32);
    color: #000000;
    font-size: 12px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }}
  .badge b {{
    color: #000000;
    font-weight: 600;
  }}
  .status-pill {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    border-radius: 999px;
    background: rgba(255,255,255,0.32);
    border: 1px solid rgba(0,0,0,0.12);
    color: #000000;
    font-size: 13px;
  }}
  .status-dot {{
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: rgba(0, 0, 0, 0.35);
    box-shadow: 0 0 0 transparent;
    transition: 0.2s ease;
  }}
  .status-pill.live .status-dot {{
    background: var(--active);
    box-shadow: 0 0 12px rgba(121, 157, 255, 0.45);
  }}
  .status-pill.error .status-dot {{
    background: var(--error);
    box-shadow: 0 0 12px rgba(255, 127, 127, 0.55);
  }}
  .hero-grid {{
    display: grid;
    grid-template-columns: minmax(0, 1.25fr) minmax(280px, 0.75fr);
    gap: 18px;
    align-items: stretch;
    flex: 1 1 auto;
    align-content: center;
  }}
  .headline {{
    font-size: 30px;
    line-height: 1.02;
    margin: 0 0 10px 0;
    letter-spacing: -0.03em;
  }}
  .lead {{
    margin: 0 0 16px 0;
    color: var(--muted);
    line-height: 1.6;
    font-size: 15px;
    max-width: 620px;
  }}
  .tip-list {{
    display: grid;
    gap: 10px;
  }}
  .tip {{
    border-radius: 16px;
    padding: 12px 14px;
    background: rgba(255,255,255,0.28);
    border: 1px solid rgba(0,0,0,0.10);
  }}
  .tip strong {{
    display: block;
    margin-bottom: 4px;
    font-size: 12px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #000000;
  }}
  .tip span {{
    color: #000000;
    line-height: 1.45;
    font-size: 14px;
  }}
  .action-card {{
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 16px;
    padding: 18px;
    border-radius: 22px;
    background: rgba(255,255,255,0.34);
    border: 1px solid rgba(121, 157, 255, 0.34);
  }}
  .action-copy {{
    color: #000000;
    font-size: 14px;
    line-height: 1.6;
  }}
  .action-copy strong {{
    display: block;
    margin-bottom: 6px;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #000000;
  }}
  .button-row {{
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    align-items: center;
  }}
  button {{
    appearance: none;
    border: none;
    font: inherit;
  }}
  #btn {{
    min-width: 148px;
    padding: 14px 20px;
    border-radius: 16px;
    background: #799DFF;
    color: #000000;
    font-weight: 700;
    letter-spacing: 0.04em;
    cursor: pointer;
    box-shadow: 0 14px 28px rgba(121, 157, 255, 0.26);
    transition: transform 0.18s ease, box-shadow 0.18s ease, filter 0.18s ease;
  }}
  #btn:hover {{
    transform: translateY(-1px);
    box-shadow: 0 18px 32px rgba(121, 157, 255, 0.32);
  }}
  #btn.active {{
    background: #799DFF;
    box-shadow: 0 16px 32px rgba(121, 157, 255, 0.32);
  }}
  #clear, #copy {{
    padding: 13px 16px;
    border-radius: 14px;
    cursor: pointer;
    background: rgba(255,255,255,0.28);
    color: #000000;
    border: 1px solid rgba(0,0,0,0.12);
    transition: background 0.18s ease, border-color 0.18s ease;
  }}
  #clear:hover, #copy:hover {{
    background: rgba(121, 157, 255, 0.20);
    border-color: rgba(121, 157, 255, 0.50);
  }}
  .voice-panel {{
    padding: 20px 24px 10px;
  }}
  .voice-meta {{
    display: flex;
    justify-content: space-between;
    gap: 10px;
    align-items: end;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }}
  .voice-title {{
    margin: 0;
    font-size: 13px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #000000;
  }}
  .voice-caption {{
    margin: 5px 0 0 0;
    color: #000000;
    font-size: 13px;
  }}
  .voice-line {{
    display: flex;
    align-items: flex-end;
    gap: 4px;
    height: 94px;
    padding: 14px 16px;
    border-radius: 20px;
    background: var(--panel-soft);
    border: 1px solid rgba(0,0,0,0.10);
    overflow: hidden;
  }}
  .voice-bar {{
    flex: 1 1 0;
    min-width: 4px;
    height: 12px;
    border-radius: 999px;
    background: #799DFF;
    transform-origin: center bottom;
    opacity: 0.35;
    box-shadow: 0 0 0 rgba(121, 157, 255, 0);
    transition: opacity 0.12s linear, box-shadow 0.12s linear;
  }}
  .transcript-panel {{
    display: grid;
    grid-template-columns: minmax(0, 1fr) 260px;
    gap: 18px;
    padding: 16px 24px 24px;
  }}
  .transcript-card {{
    border-radius: 22px;
    padding: 18px 18px 20px;
    background: rgba(255,255,255,0.28);
    border: 1px solid rgba(0,0,0,0.10);
    min-height: 248px;
  }}
  .card-label {{
    display: block;
    margin-bottom: 10px;
    color: #000000;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
  }}
  .transcript-wrap {{
    min-height: 170px;
    font-size: 22px;
    line-height: 1.55;
    color: #000000;
    word-break: break-word;
  }}
  .empty {{
    color: rgba(0, 0, 0, 0.58);
  }}
  .pending {{
    color: #000000;
  }}
  .cursor {{
    display: inline-block;
    width: 2px;
    height: 1em;
    background: var(--accent);
    margin-left: 4px;
    vertical-align: text-bottom;
    opacity: 0;
  }}
  .cursor.visible {{
    opacity: 1;
    animation: blink 1s step-end infinite;
  }}
  @keyframes blink {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0; }}
  }}
  .side-column {{
    display: grid;
    gap: 14px;
  }}
  .metric-card {{
    border-radius: 20px;
    padding: 16px;
    background: rgba(255,255,255,0.28);
    border: 1px solid rgba(0,0,0,0.10);
  }}
  .metric-card small {{
    display: block;
    margin-bottom: 6px;
    color: rgba(0, 0, 0, 0.72);
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }}
  .metric-card strong {{
    display: block;
    color: #000000;
    font-size: 28px;
    line-height: 1.05;
  }}
  .metric-card span {{
    display: block;
    margin-top: 6px;
    color: #000000;
    font-size: 13px;
    line-height: 1.45;
  }}
  .metric-tone-fast strong {{ color: #000000; }}
  .metric-tone-ok strong {{ color: #000000; }}
  .metric-tone-slow strong {{ color: #000000; }}
  .footnote {{
    padding: 0 24px 24px;
    color: #000000;
    font-size: 12px;
    line-height: 1.6;
  }}
  @media (max-width: 900px) {{
    .hero-grid,
    .transcript-panel {{
      grid-template-columns: 1fr;
    }}
    .headline {{
      font-size: 25px;
    }}
    .transcript-wrap {{
      font-size: 19px;
    }}
    .voice-line {{
      height: 80px;
      gap: 3px;
    }}
    .voice-bar:nth-child(odd) {{
      min-width: 3px;
    }}
  }}
</style>
</head>
<body>
  <div class="shell">
    <div class="panel">
      <section class="hero">
        <div class="topline">
          <div class="badge-row">
            <span class="badge">Источник <b>{audio_label}</b></span>
            <span class="badge">Голос <b>{ref_audio_label}</b></span>
            <span class="badge">Вывод <b id="badge-lang">EN</b></span>
          </div>
          <div class="status-pill" id="status">
            <span class="status-dot"></span>
            <span id="status-text">Ожидание запуска</span>
          </div>
        </div>
        <div class="hero-grid">
          <div>
            <h2 class="headline">Текст остаётся основным результатом. Озвучка и анимация помогают понять, что происходит прямо сейчас.</h2>
            <p class="lead" id="helper-text">
              Нажмите запуск, затем говорите спокойно. Если используете микрофон, делайте короткую паузу между фразами,
              чтобы перевод успевал стабилизироваться.
            </p>
            <div class="tip-list">
              <div class="tip">
                <strong>Говорите медленно</strong>
                <span>Не ускоряйтесь в середине фразы. Так меньше ошибок в хвосте текста.</span>
              </div>
              <div class="tip">
                <strong>Следите за строкой</strong>
                <span>Когда линия оживает, система слышит вас или проигрывает ответ.</span>
              </div>
            </div>
          </div>
          <aside class="action-card">
            <div class="action-copy">
              <strong>Быстрый сценарий</strong>
              Запустите с микрофона для живой речи или загрузите аудиофайл. Очистка не сбрасывает соединение, она очищает только текст.
            </div>
            <div class="button-row">
              <button id="btn" onclick="toggle()">Начать</button>
              <button id="clear" onclick="clearTranscript()">Очистить</button>
              <button id="copy" onclick="copyTranscript()">Скопировать</button>
            </div>
          </aside>
        </div>
      </section>

      <section class="voice-panel">
        <div class="voice-meta">
          <div>
            <p class="voice-title">Голосовая строка</p>
            <p class="voice-caption">Анимация реагирует на микрофон, аудиофайл и воспроизведение TTS.</p>
          </div>
          <div class="badge-row">
            <span class="badge">Режим <b id="mode-label">Микрофон</b></span>
          </div>
        </div>
        <div class="voice-line" id="voice-line">{wave_bars_html}</div>
      </section>

      <section class="transcript-panel">
        <div class="transcript-card">
          <span class="card-label">Поток текста</span>
          <div class="transcript-wrap">
            <span id="stable" class="empty">После запуска здесь появится подтверждённый текст перевода.</span>
            <span id="pending"></span>
            <span id="cursor" class="cursor"></span>
          </div>
        </div>
        <aside class="side-column">
          <div class="metric-card" id="metric-chars">
            <small>Символов</small>
            <strong id="stat-chars">0</strong>
            <span>Количество подтверждённого текста.</span>
          </div>
          <div class="metric-card" id="metric-words">
            <small>Слов</small>
            <strong id="stat-words">0</strong>
            <span>Считается по подтверждённой и текущей части.</span>
          </div>
          <div class="metric-card" id="metric-model">
            <small>Время модели</small>
            <strong id="stat-lat">-</strong>
            <span id="stat-lat-note">Ждём первые замеры после запуска.</span>
          </div>
          <div class="metric-card" id="metric-avg">
            <small>Средняя задержка</small>
            <strong id="stat-avg">-</strong>
            <span id="stat-log">Служебные сообщения появятся здесь.</span>
          </div>
        </aside>
      </section>

      <div class="footnote">
        Подтверждённый текст остаётся в светлом по контрасту текстовом поле. Нестабильный хвост подсвечивается отдельно,
        поэтому пользователь видит, что система ещё дослушивает фразу.
      </div>
    </div>
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
let playbackActiveUntil = 0;
let voiceLevel = 0.04;
let animationFrame = null;

const statusEl = document.getElementById("status");
const statusTextEl = document.getElementById("status-text");
const stableEl = document.getElementById("stable");
const pendingEl = document.getElementById("pending");
const cursorEl = document.getElementById("cursor");
const helperTextEl = document.getElementById("helper-text");
const modeLabelEl = document.getElementById("mode-label");
const voiceBars = Array.from(document.querySelectorAll(".voice-bar"));

function setStatus(text, cls = "", helper = "") {{
  statusEl.className = "status-pill" + (cls ? " " + cls : "");
  statusTextEl.textContent = text;
  if (helper) helperTextEl.textContent = helper;
}}

function setBtn(active) {{
  const btn = document.getElementById("btn");
  btn.className = active ? "active" : "";
  btn.textContent = active ? "Остановить" : "Начать";
}}

function setCursor(visible) {{
  cursorEl.className = "cursor" + (visible ? " visible" : "");
}}

function setModeLabel(text) {{
  modeLabelEl.textContent = text;
}}

function decayVoiceLevel() {{
  voiceLevel *= recording ? 0.88 : 0.83;
  if (voiceLevel < 0.04) voiceLevel = 0.04;
}}

function pushVoiceLevel(level) {{
  const safe = Math.max(0.04, Math.min(1, level || 0));
  voiceLevel = Math.max(voiceLevel, safe);
}}

function animateVoiceLine(now = 0) {{
  const active = recording || now < playbackActiveUntil;
  const drift = active ? 0.22 : 0.05;
  voiceBars.forEach((bar, index) => {{
    const waveA = (Math.sin(now * 0.009 + index * 0.42) + 1) / 2;
    const waveB = (Math.cos(now * 0.0045 + index * 0.21) + 1) / 2;
    const intensity = active ? voiceLevel : 0.05;
    const height = 10 + ((intensity + drift) * 54 * (0.28 + waveA * 0.72)) + waveB * 5;
    const opacity = active ? (0.32 + intensity * 0.7) : 0.25;
    bar.style.height = `${{Math.min(height, 78)}}px`;
    bar.style.opacity = opacity.toFixed(3);
    bar.style.boxShadow = active
      ? "0 0 12px rgba(255, 170, 92, 0.18)"
      : "0 0 0 rgba(255, 170, 92, 0)";
  }});
  decayVoiceLevel();
  animationFrame = window.requestAnimationFrame(animateVoiceLine);
}}

function setTranscript(stable, pending) {{
  const stableText = (stable || "").trim();
  stableEl.textContent = stableText || "После запуска здесь появится подтверждённый текст перевода.";
  stableEl.className = stableText ? "" : "empty";
  pendingEl.textContent = pending ? " " + pending : "";
  pendingEl.className = pending ? "pending" : "";

  clearTimeout(pendingTimer);
  if (pending) {{
    pendingTimer = setTimeout(() => {{
      pendingEl.style.opacity = "0.82";
    }}, 240);
  }} else {{
    pendingEl.style.opacity = "1";
  }}
}}

function setMetricTone(id, valueMs) {{
  const el = document.getElementById(id);
  el.className = "metric-card";
  if (valueMs === null) return;
  if (valueMs < 800) el.classList.add("metric-tone-fast");
  else if (valueMs < 1500) el.classList.add("metric-tone-ok");
  else el.classList.add("metric-tone-slow");
}}

function updateStats(stable, pending, log) {{
  const full = ((stable || "") + " " + (pending || "")).trim();
  document.getElementById("stat-chars").textContent = (stable || "").length;
  document.getElementById("stat-words").textContent = full ? full.split(/\\s+/).filter(Boolean).length : 0;
  document.getElementById("stat-log").textContent = log || "Служебные сообщения появятся здесь.";

  const modelMatch = log && log.match(/model\\s+(\\d+)ms/i);
  const avgMatch = log && log.match(/avg\\s+(\\d+)ms/i);
  const modelMs = modelMatch ? parseInt(modelMatch[1], 10) : null;
  const avgMs = avgMatch ? parseInt(avgMatch[1], 10) : null;

  document.getElementById("stat-lat").textContent = modelMs !== null ? modelMs + " ms" : "-";
  document.getElementById("stat-avg").textContent = avgMs !== null ? avgMs + " ms" : "-";
  document.getElementById("stat-lat-note").textContent =
    modelMs !== null ? "Обновляется по логам backend/STT." : "Ждём первые замеры после запуска.";

  setMetricTone("metric-model", modelMs);
  setMetricTone("metric-avg", avgMs);
}}

function clearTranscript() {{
  setTranscript("", "");
  updateStats("", "", "");
}}

function copyTranscript() {{
  const text = (stableEl.textContent || "").trim();
  if (!text || stableEl.classList.contains("empty")) return;
  navigator.clipboard.writeText(text).catch(() => {{}});
}}

function measureFloat32Level(samples) {{
  if (!samples || !samples.length) return 0.04;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  const rms = Math.sqrt(sum / samples.length);
  return Math.min(1, 0.12 + rms * 8.5);
}}

function pcm16ToFloat32(int16) {{
  const out = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) out[i] = int16[i] / 32768;
  return out;
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

async function schedulePcmFrame(buffer) {{
  await ensurePlaybackContext();
  const view = new DataView(buffer);
  const pcmLen = view.getUint32(8, true);
  const pcm = new Int16Array(buffer, 12, pcmLen / 2);
  const float32 = pcm16ToFloat32(pcm);
  pushVoiceLevel(measureFloat32Level(float32));

  const audioBuffer = playbackCtx.createBuffer(1, float32.length, playbackSampleRate);
  audioBuffer.copyToChannel(float32, 0);
  const src = playbackCtx.createBufferSource();
  src.buffer = audioBuffer;
  src.connect(playbackCtx.destination);
  const startAt = Math.max(nextPlaybackTime, playbackCtx.currentTime + 0.02);
  src.start(startAt);
  nextPlaybackTime = startAt + audioBuffer.duration;
  playbackActiveUntil = performance.now() + Math.max(audioBuffer.duration * 1000 + 140, 240);
}}

function b64ToBytes(b64) {{
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr;
}}

async function streamFile() {{
  setModeLabel("Файл");
  setStatus(
    "Декодируем файл",
    "",
    "Аудиофайл разбивается на небольшие чанки и отправляется в backend с той же логикой, что и микрофон."
  );

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
  await decCtx.close().catch(() => {{}});

  setStatus(
    "Отправляем аудио",
    "live",
    "Фронт сейчас стримит звук в backend. Текст и озвучка будут появляться по мере обработки."
  );

  const chunkN = Math.floor(SAMPLE_RATE * CHUNK_SEC);
  for (let i = 0; i < samples.length && recording; i += chunkN) {{
    const chunk = new Float32Array(samples.subarray(i, Math.min(i + chunkN, samples.length)));
    pushVoiceLevel(measureFloat32Level(chunk));
    if (ws?.readyState === WebSocket.OPEN) ws.send(chunk.buffer);
    await new Promise((resolve) => setTimeout(resolve, CHUNK_SEC * 1000));
  }}

  if (recording && ws?.readyState === WebSocket.OPEN) {{
    ws.send(JSON.stringify({{ event: "session_end" }}));
    setStatus(
      "Файл отправлен",
      "live",
      "Новые куски аудио больше не отправляются. Система завершает оставшуюся обработку."
    );
  }}
}}

async function startMic() {{
  setModeLabel("Микрофон");
  micStream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
  inputCtx = new AudioContext({{ sampleRate: SAMPLE_RATE }});
  micSrc = inputCtx.createMediaStreamSource(micStream);
  processor = inputCtx.createScriptProcessor(4096, 1, 1);

  const chunkN = Math.floor(SAMPLE_RATE * CHUNK_SEC);
  processor.onaudioprocess = (e) => {{
    const data = e.inputBuffer.getChannelData(0);
    pushVoiceLevel(measureFloat32Level(data));
    inputBuf.push(...data);
    while (inputBuf.length >= chunkN) {{
      const chunk = new Float32Array(inputBuf.splice(0, chunkN));
      if (ws?.readyState === WebSocket.OPEN) ws.send(chunk.buffer);
    }}
  }};

  micSrc.connect(processor);
  processor.connect(inputCtx.destination);
  setStatus(
    "Слушаем микрофон",
    "live",
    "Говорите спокойно и чуть медленнее обычного. Короткая пауза между фразами улучшает стабильность текста."
  );
}}

function toggle() {{
  recording ? stop() : start();
}}

async function start() {{
  if (animationFrame === null) animationFrame = window.requestAnimationFrame(animateVoiceLine);
  recording = true;
  setBtn(true);
  setCursor(true);
  clearTranscript();
  setStatus("Подключаемся", "", "Открываем websocket к backend и подготавливаем аудио.");
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

    let payload = null;
    try {{
      payload = JSON.parse(e.data);
    }} catch {{
      return;
    }}

    if ((payload.event === "translation" || payload.stable !== undefined) && payload.stable !== undefined) {{
      setTranscript(payload.stable, payload.pending || "");
      updateStats(payload.stable, payload.pending || "", payload.speed_logs || "");
      if (payload.lang) document.getElementById("badge-lang").textContent = String(payload.lang).toUpperCase();
      return;
    }}

    if (payload.event === "started" && payload.sample_rate) {{
      playbackSampleRate = Number(payload.sample_rate) || TTS_DEFAULT_SR;
      return;
    }}

    if (payload.event === "error") {{
      setStatus(
        "Ошибка backend",
        "error",
        "Соединение установлено, но downstream-сервис вернул ошибку. Проверьте backend, STT и TTS."
      );
      return;
    }}

    if (payload.event === "done") {{
      setStatus(
        "Обработка завершена",
        "",
        "Запись завершилась. Можно скопировать текст или сразу запустить следующую сессию."
      );
    }}
  }};

  ws.onerror = () => {{
    setStatus(
      "Ошибка websocket",
      "error",
      "Фронт не смог стабильно работать с backend. Проверьте адрес websocket и состояние сервиса."
    );
  }};

  ws.onclose = () => {{
    if (recording) {{
      setStatus(
        "Соединение закрыто",
        "error",
        "Во время активной сессии websocket закрылся. Обычно это значит, что backend или downstream-сервис остановился."
      );
    }}
  }};
}}

function stop() {{
  recording = false;
  setBtn(false);
  setCursor(false);
  setStatus(
    "Сессия остановлена",
    "",
    "Новые аудиоданные больше не отправляются. Если backend ещё дорабатывает хвост, текст может обновиться."
  );
  inputBuf = [];

  clearTimeout(pendingTimer);
  if (processor) processor.disconnect();
  if (micSrc) micSrc.disconnect();
  if (micStream) micStream.getTracks().forEach((track) => track.stop());
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

setModeLabel(HAS_FILE ? "Файл" : "Микрофон");
if (animationFrame === null) animationFrame = window.requestAnimationFrame(animateVoiceLine);
</script>
</body>
</html>
""",
    height=1500,
    scrolling=True,
)
