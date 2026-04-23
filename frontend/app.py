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
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


REFERENCE_AUDIO_DIR = Path(__file__).resolve().parent / "reference_audio"
REFERENCE_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
CURRENT_REFERENCE_WAV = REFERENCE_AUDIO_DIR / "current_reference.wav"
CURRENT_REFERENCE_META = REFERENCE_AUDIO_DIR / "current_reference_meta.json"

REFERENCE_PROMPTS = {
    "Russian": (
        "Сегодня, в 19:45, я спокойно объясню проект: цифры 12345, адрес e-mail@example.com, "
        "а также паузы, интонацию и знаки — запятая, двоеточие, тире, вопрос и восклицание!"
    ),
    "English": (
        "At exactly 7:45 p.m., I will clearly explain the project: numbers 12345, "
        "an e-mail like sample.user@example.com, and punctuation - commas, colons, dashes, "
        "questions, and exclamations!"
    ),
}


def save_reference_wav(filename: str, data: bytes) -> Path:
    path = REFERENCE_AUDIO_DIR / filename
    path.write_bytes(data)
    return path


def save_current_reference(data: bytes, source: str, lang: str) -> Path:
    CURRENT_REFERENCE_WAV.write_bytes(data)
    CURRENT_REFERENCE_META.write_text(
        json.dumps({"source": source, "lang": lang}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return CURRENT_REFERENCE_WAV


def load_current_reference_meta() -> dict:
    if not CURRENT_REFERENCE_META.exists():
        return {}
    try:
        return json.loads(CURRENT_REFERENCE_META.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

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
    phrase_language = st.segmented_control(
        "Язык референсной фразы",
        options=["Russian", "English"],
        default="Russian",
        help="Выберите язык фразы, которую нужно произнести при записи референса.",
    )
    selected_reference_phrase = REFERENCE_PROMPTS[phrase_language]
    st.text_area(
        "Фраза для записи (прочитайте чётко, с пунктуацией)",
        value=selected_reference_phrase,
        height=120,
        disabled=True,
    )
    ref_text_value = st.text_input(
        "Референсный текст для TTS",
        value=selected_reference_phrase,
    )

if "ref_mode" not in st.session_state:
    st.session_state.ref_mode = None
if "saved_reference_path" not in st.session_state:
    st.session_state.saved_reference_path = None
if "saved_reference_source" not in st.session_state:
    st.session_state.saved_reference_source = None
if "saved_reference_lang" not in st.session_state:
    st.session_state.saved_reference_lang = None

st.markdown("### Шаг 1. Выберите источник референсного голоса")
ref_choice_left, ref_choice_right = st.columns(2, gap="large")
with ref_choice_left:
    if st.button("Выбрать референсное аудио", use_container_width=True):
        st.session_state.ref_mode = "upload"
with ref_choice_right:
    if st.button("Записать аудио сейчас", use_container_width=True):
        st.session_state.ref_mode = "record"

recorded_ref = None
with st.expander("Источник аудио и настройки голоса", expanded=True):
    uploads_left, uploads_right = st.columns(2, gap="large")
    with uploads_left:
        uploaded = st.file_uploader(
            "Аудиофайл вместо микрофона",
            type=["wav", "mp3", "m4a", "ogg", "webm", "flac"],
            help="Если файл не выбран, интерфейс будет слушать микрофон.",
        )
    with uploads_right:
        if st.session_state.ref_mode == "upload":
            ref_uploaded = st.file_uploader(
                "Референсное аудио для TTS",
                type=["wav"],
                help="Файл будет сохранён как WAV в папку frontend/reference_audio.",
                key="ref_uploaded_wav",
            )
        elif st.session_state.ref_mode == "record":
            st.markdown("Скажите следующую фразу для записи референса:")
            st.code(selected_reference_phrase, language=None)
            ref_uploaded = None
            recorded_ref = st.audio_input(
                "Нажмите и запишите референсный голос",
                key="recorded_reference_audio",
            )
        else:
            ref_uploaded = None
            st.caption("Сначала выберите один из двух вариантов выше.")

audio_b64 = ""
audio_name = "Микрофон"
if uploaded is not None:
    audio_b64 = base64.b64encode(uploaded.getvalue()).decode("ascii")
    audio_name = uploaded.name

ref_audio_b64 = ""
ref_audio_name = "Голос по умолчанию"
saved_reference_path = None
if st.session_state.ref_mode == "upload" and ref_uploaded is not None:
    ref_bytes = ref_uploaded.getvalue()
    save_reference_wav("uploaded_reference.wav", ref_bytes)
    saved_reference_path = save_current_reference(ref_bytes, "Загруженный WAV", phrase_language)
    ref_audio_b64 = base64.b64encode(ref_bytes).decode("ascii")
    ref_audio_name = ref_uploaded.name
    st.session_state.saved_reference_path = str(saved_reference_path)
    st.session_state.saved_reference_source = "Загруженный WAV"
    st.session_state.saved_reference_lang = phrase_language
elif st.session_state.ref_mode == "record" and recorded_ref is not None:
    ref_bytes = recorded_ref.getvalue()
    save_reference_wav("recorded_reference.wav", ref_bytes)
    saved_reference_path = save_current_reference(ref_bytes, "Записанный голос", phrase_language)
    ref_audio_b64 = base64.b64encode(ref_bytes).decode("ascii")
    ref_audio_name = CURRENT_REFERENCE_WAV.name
    st.session_state.saved_reference_path = str(saved_reference_path)
    st.session_state.saved_reference_source = "Записанный голос"
    st.session_state.saved_reference_lang = phrase_language
elif st.session_state.saved_reference_path:
    saved_reference_path = Path(st.session_state.saved_reference_path)
    if saved_reference_path.exists():
        ref_bytes = saved_reference_path.read_bytes()
        ref_audio_b64 = base64.b64encode(ref_bytes).decode("ascii")
        ref_audio_name = saved_reference_path.name
elif CURRENT_REFERENCE_WAV.exists():
    saved_reference_path = CURRENT_REFERENCE_WAV
    ref_bytes = saved_reference_path.read_bytes()
    ref_audio_b64 = base64.b64encode(ref_bytes).decode("ascii")
    ref_audio_name = saved_reference_path.name
    loaded_meta = load_current_reference_meta()
    st.session_state.saved_reference_path = str(saved_reference_path)
    st.session_state.saved_reference_source = loaded_meta.get("source") or "Референс с прошлого запуска"
    st.session_state.saved_reference_lang = loaded_meta.get("lang") or phrase_language

if saved_reference_path is not None and saved_reference_path.exists():
    saved_lang = st.session_state.saved_reference_lang or phrase_language
    st.info(
        "Референсный голос сохранён: "
        f"{saved_reference_path} "
        f"({st.session_state.saved_reference_source or 'Референс'}, язык: {saved_lang})"
    )

if not ref_audio_b64:
    st.markdown(
        """
<div class="section-note">
  Сейчас продиктованный голос не используется, пока его не записать или не загрузить.
  После подготовки референса основной экран перевода появится ниже автоматически.
</div>
""",
        unsafe_allow_html=True,
    )
    st.stop()

st.markdown(
    """
<div class="section-note">
  Референсный голос уже сохранён как `.wav`. Ниже открывается основной экран перевода,
  и backend получает именно этот сохранённый референс.
</div>
""",
    unsafe_allow_html=True,
)

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
  .is-hidden {{
    display: none !important;
  }}
  .setup-screen {{
    padding: 30px 24px 24px;
  }}
  .setup-shell {{
    border-radius: 24px;
    background: rgba(255, 248, 242, 0.72);
    border: 1px solid rgba(0, 0, 0, 0.10);
    padding: 28px 24px;
  }}
  .setup-eyebrow {{
    margin: 0 0 10px 0;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: rgba(0, 0, 0, 0.62);
  }}
  .setup-title {{
    margin: 0 0 12px 0;
    font-size: 34px;
    line-height: 1.02;
    letter-spacing: -0.03em;
  }}
  .setup-copy {{
    margin: 0 0 18px 0;
    max-width: 760px;
    font-size: 15px;
    line-height: 1.6;
  }}
  .setup-grid {{
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px;
    margin-bottom: 18px;
  }}
  .setup-option {{
    display: block;
    width: 100%;
    text-align: left;
    padding: 18px 18px 16px;
    border-radius: 20px;
    background: rgba(255,255,255,0.34);
    border: 1px solid rgba(121, 157, 255, 0.34);
    cursor: pointer;
    transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
  }}
  .setup-option:hover {{
    transform: translateY(-1px);
    box-shadow: 0 12px 24px rgba(121, 157, 255, 0.18);
    border-color: rgba(121, 157, 255, 0.58);
  }}
  .setup-option strong {{
    display: block;
    margin-bottom: 8px;
    font-size: 18px;
    line-height: 1.2;
    color: #000000;
  }}
  .setup-option span {{
    display: block;
    color: rgba(0, 0, 0, 0.82);
    font-size: 14px;
    line-height: 1.55;
  }}
  .setup-footer {{
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
    justify-content: space-between;
  }}
  .setup-note {{
    margin: 0;
    font-size: 14px;
    line-height: 1.5;
    color: rgba(0, 0, 0, 0.72);
  }}
  .setup-note.error {{
    color: #b94a48;
  }}
  .setup-pill {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 9px 12px;
    border-radius: 999px;
    background: rgba(255,255,255,0.34);
    border: 1px solid rgba(0,0,0,0.10);
    font-size: 13px;
  }}
  .setup-pill b {{
    font-weight: 700;
  }}
  .record-phrases {{
    display: grid;
    gap: 10px;
    margin: 0 0 18px 0;
  }}
  .phrase {{
    padding: 14px 16px;
    border-radius: 18px;
    background: rgba(255,255,255,0.34);
    border: 1px solid rgba(0,0,0,0.10);
    font-size: 16px;
    line-height: 1.5;
  }}
  .setup-actions {{
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
  }}
  .choice-btn,
  .ghost-btn {{
    padding: 14px 18px;
    border-radius: 16px;
    cursor: pointer;
    transition: transform 0.18s ease, box-shadow 0.18s ease, background 0.18s ease, border-color 0.18s ease;
  }}
  .choice-btn {{
    background: #799DFF;
    color: #000000;
    box-shadow: 0 14px 28px rgba(121, 157, 255, 0.26);
  }}
  .choice-btn:hover {{
    transform: translateY(-1px);
    box-shadow: 0 18px 32px rgba(121, 157, 255, 0.32);
  }}
  .ghost-btn {{
    background: rgba(255,255,255,0.28);
    color: #000000;
    border: 1px solid rgba(0,0,0,0.12);
  }}
  .ghost-btn:hover {{
    background: rgba(121, 157, 255, 0.20);
    border-color: rgba(121, 157, 255, 0.50);
  }}
  .choice-btn.recording {{
    background: #b94a48;
    box-shadow: 0 14px 28px rgba(185, 74, 72, 0.24);
  }}
  .recorder-status {{
    margin: 14px 0 0 0;
    font-size: 14px;
    line-height: 1.5;
    color: rgba(0, 0, 0, 0.72);
  }}
  .recorder-status.live {{
    color: #000000;
  }}
  .recorder-status.error {{
    color: #b94a48;
  }}
  .main-app {{
    display: block;
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
    .setup-grid,
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
      <section id="setup-screen" class="setup-screen">
        <div id="setup-choice" class="setup-shell">
          <p class="setup-eyebrow">Шаг 1</p>
          <h2 class="setup-title">Сначала выберите референсный голос</h2>
          <p class="setup-copy">
            Перед основной записью нужно указать, каким голосом будет работать TTS.
            Можно использовать заранее загруженный WAV-файл или записать короткий референс прямо сейчас.
          </p>
          <div class="setup-grid">
            <button id="choose-upload" class="setup-option" type="button">
              <strong>Выбрать референсное аудио</strong>
              <span>Использовать WAV, который загружен выше в поле «Референсное аудио для TTS».</span>
            </button>
            <button id="choose-record" class="setup-option" type="button">
              <strong>Записать аудио сейчас</strong>
              <span>Откроется отдельное окно записи, и вы сможете сразу продиктовать три подготовленные фразы.</span>
            </button>
          </div>
          <div class="setup-footer">
            <span class="setup-pill">Источник перевода <b>{audio_label}</b></span>
            <p id="setup-note" class="setup-note">Сделайте выбор, прежде чем запускать основную сессию перевода.</p>
          </div>
        </div>

        <div id="setup-recorder" class="setup-shell is-hidden">
          <p class="setup-eyebrow">Запись референса</p>
          <h2 class="setup-title">Скажите три предложения для записи</h2>
          <p class="setup-copy">
            Нажмите кнопку записи, прочитайте фразы спокойно и без спешки, затем остановите запись.
            Эта запись будет использована как референсный голос вместо загруженного файла.
          </p>
          <div class="record-phrases">
            <div class="phrase">1. «Какая сегодня погода погоды в твоем городе?»</div>
            <div class="phrase">2. «Отлично, я помогу тебе с этим прямо сейчас!»</div>
            <div class="phrase">3. «Вот что я нашла по твоему запросу, слушай внимательно.»</div>
          </div>
          <div class="setup-actions">
            <button id="setup-back" class="ghost-btn" type="button">Назад</button>
            <button id="setup-record-btn" class="choice-btn" type="button">Начать запись</button>
          </div>
          <p id="recorder-status" class="recorder-status">Запись ещё не началась.</p>
        </div>
      </section>

      <div id="main-app" class="main-app is-hidden">
      <section class="hero">
        <div class="topline">
          <div class="badge-row">
            <span class="badge">Источник <b>{audio_label}</b></span>
            <span class="badge">Голос <b id="ref-badge-label">{ref_audio_label}</b></span>
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
  </div>

<script>
const WS_URL = {json.dumps(ws_url)};
const HAS_FILE = {str(bool(audio_b64)).lower()};
const AUDIO_B64 = {json.dumps(audio_b64)};
const REF_AUDIO_B64 = {json.dumps(ref_audio_b64)};
const UPLOADED_REF_LABEL = {json.dumps(ref_audio_name if ref_audio_b64 else "")};
const REF_TEXT = {json.dumps(ref_text_value)};
const REF_LANG = {json.dumps(phrase_language)};

const SAMPLE_RATE = 16000;
const CHUNK_SEC = 0.1;
const TTS_DEFAULT_SR = 24000;

let ws = null;
let recording = false;
let inputCtx = null;
let micStream = null;
let micSrc = null;
let processor = null;
let pendingTimer = null;

let playbackCtx = null;
let playbackSampleRate = TTS_DEFAULT_SR;
let nextPlaybackTime = 0;
let playbackActiveUntil = 0;
let voiceLevel = 0.04;
let animationFrame = null;
let runtimeRefAudioB64 = REF_AUDIO_B64 || "";
let runtimeRefLabel = REF_AUDIO_B64 ? (UPLOADED_REF_LABEL || "Загруженный референс") : "Не выбран";
let setupComplete = true;

let refRecorderStream = null;
let refRecorderCtx = null;
let refRecorderSource = null;
let refRecorderProcessor = null;
let refRecorderMute = null;
let refRecorderChunks = [];
let refRecorderSampleRate = SAMPLE_RATE;
let refRecorderActive = false;
let refRecorderCapture = null;

const statusEl = document.getElementById("status");
const statusTextEl = document.getElementById("status-text");
const stableEl = document.getElementById("stable");
const pendingEl = document.getElementById("pending");
const cursorEl = document.getElementById("cursor");
const helperTextEl = document.getElementById("helper-text");
const modeLabelEl = document.getElementById("mode-label");
const refBadgeLabelEl = document.getElementById("ref-badge-label");
const voiceBars = Array.from(document.querySelectorAll(".voice-bar"));
const setupScreenEl = document.getElementById("setup-screen");
const setupChoiceEl = document.getElementById("setup-choice");
const setupRecorderEl = document.getElementById("setup-recorder");
const mainAppEl = document.getElementById("main-app");
const setupNoteEl = document.getElementById("setup-note");
const recorderStatusEl = document.getElementById("recorder-status");
const setupRecordBtn = document.getElementById("setup-record-btn");
const chooseUploadBtn = document.getElementById("choose-upload");
const chooseRecordBtn = document.getElementById("choose-record");
const setupBackBtn = document.getElementById("setup-back");

/* ─────────────────────────────────────────────
   ИСПРАВЛЕНИЕ 1: Float32 → Int16 PCM конвертация
   (аналог floatTo16BitPCM из рабочего HTML-клиента)
───────────────────────────────────────────── */
function floatTo16BitPCM(float32Array) {{
  const buffer = new ArrayBuffer(float32Array.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < float32Array.length; i++) {{
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }}
  return buffer;
}}

/* ─────────────────────────────────────────────
   ИСПРАВЛЕНИЕ 2: downsample с нативной частоты до 16k
   (аналог downsampleBuffer из рабочего HTML-клиента)
───────────────────────────────────────────── */
function downsampleBuffer(buffer, inputRate, outputRate) {{
  if (outputRate === inputRate) return buffer;
  const ratio = inputRate / outputRate;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLength);
  let offsetBuffer = 0;
  for (let i = 0; i < newLength; i++) {{
    const nextOffsetBuffer = Math.round((i + 1) * ratio);
    let accum = 0, count = 0;
    for (let j = offsetBuffer; j < nextOffsetBuffer && j < buffer.length; j++) {{
      accum += buffer[j];
      count++;
    }}
    result[i] = count > 0 ? accum / count : 0;
    offsetBuffer = nextOffsetBuffer;
  }}
  return result;
}}

function setVisibility(el, visible) {{
  if (!el) return;
  el.classList.toggle("is-hidden", !visible);
}}

function updateReferenceBadge() {{
  if (refBadgeLabelEl) refBadgeLabelEl.textContent = runtimeRefLabel;
}}

function setSetupNote(text, isError = false) {{
  if (!setupNoteEl) return;
  setupNoteEl.textContent = text;
  setupNoteEl.className = "setup-note" + (isError ? " error" : "");
}}

function setRecorderStatus(text, tone = "") {{
  if (!recorderStatusEl) return;
  recorderStatusEl.textContent = text;
  recorderStatusEl.className = "recorder-status" + (tone ? " " + tone : "");
}}

function showSetupChoice() {{
  setVisibility(setupChoiceEl, true);
  setVisibility(setupRecorderEl, false);
}}

function showSetupRecorder() {{
  setVisibility(setupChoiceEl, false);
  setVisibility(setupRecorderEl, true);
  setRecorderStatus(
    "Нажмите «Начать запись», затем спокойно прочитайте все три предложения подряд.",
    ""
  );
}}

function finishSetup(helperText) {{
  setupComplete = true;
  setVisibility(setupScreenEl, false);
  setVisibility(mainAppEl, true);
  updateReferenceBadge();
  setStatus("Готово к запуску", "", helperText);
}}

function chooseUploadedReference() {{
  if (!REF_AUDIO_B64) {{
    setSetupNote(
      "Сначала загрузите WAV-файл в поле «Референсное аудио для TTS» выше, либо выберите запись референса прямо сейчас.",
      true
    );
    return;
  }}
  runtimeRefAudioB64 = REF_AUDIO_B64;
  runtimeRefLabel = UPLOADED_REF_LABEL || "Загруженный референс";
  finishSetup("Референсный файл выбран. Теперь можно запускать основную запись и перевод.");
}}

function mergeFloat32Chunks(chunks) {{
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) {{
    merged.set(chunk, offset);
    offset += chunk.length;
  }}
  return merged;
}}

function writeAscii(view, offset, text) {{
  for (let i = 0; i < text.length; i++) {{
    view.setUint8(offset + i, text.charCodeAt(i));
  }}
}}

function encodeWav(samples, sampleRate) {{
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {{
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    const int16 = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    view.setInt16(offset, int16, true);
  }}
  return buffer;
}}

function arrayBufferToBase64(buffer) {{
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += chunkSize) {{
    const chunk = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode(...chunk);
  }}
  return btoa(binary);
}}

async function cleanupReferenceRecorder() {{
  if (refRecorderCapture) await closeMicrophoneCapture(refRecorderCapture);

  refRecorderProcessor = null;
  refRecorderSource = null;
  refRecorderMute = null;
  refRecorderStream = null;
  refRecorderCtx = null;
  refRecorderCapture = null;
}}

async function requestMicrophoneStream() {{
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {{
    return navigator.mediaDevices.getUserMedia({{ audio: true }});
  }}

  const legacyGetUserMedia =
    navigator.getUserMedia ||
    navigator.webkitGetUserMedia ||
    navigator.mozGetUserMedia ||
    navigator.msGetUserMedia;

  if (!legacyGetUserMedia) {{
    throw new Error("getUserMedia is not available in this browser context");
  }}

  return new Promise((resolve, reject) => {{
    legacyGetUserMedia.call(navigator, {{ audio: true }}, resolve, reject);
  }});
}}

/* ─────────────────────────────────────────────
   ИСПРАВЛЕНИЕ 3: openMicrophoneCapture
   - AudioContext без принудительного sampleRate (нативная частота браузера)
   - downsample до 16k внутри onaudioprocess
   - конвертация Float32 → Int16 PCM перед вызовом onChunk
   - onChunk теперь получает ArrayBuffer (готовый PCM16), а не Float32Array
───────────────────────────────────────────── */
async function openMicrophoneCapture(onChunk) {{
  const stream = await requestMicrophoneStream();
  // Используем нативную частоту браузера, как в рабочем HTML-клиенте
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const source = ctx.createMediaStreamSource(stream);
  const processorNode = ctx.createScriptProcessor(4096, 1, 1);

  processorNode.onaudioprocess = (event) => {{
    const input = event.inputBuffer.getChannelData(0);
    // Шаг 1: downsample с нативной частоты до 16000
    const downsampled = downsampleBuffer(input, ctx.sampleRate, SAMPLE_RATE);
    // Шаг 2: измерить уровень до конвертации
    pushVoiceLevel(measureFloat32Level(downsampled));
    // Шаг 3: конвертировать Float32 → Int16 PCM (little-endian)
    const pcm16Buffer = floatTo16BitPCM(downsampled);
    // Передаём ArrayBuffer — бэкенд ожидает именно этот формат
    onChunk(pcm16Buffer);
  }};

  source.connect(processorNode);
  processorNode.connect(ctx.destination);

  return {{
    stream,
    ctx,
    source,
    processor: processorNode,
  }};
}}

async function closeMicrophoneCapture(capture) {{
  if (!capture) return;
  if (capture.processor) capture.processor.disconnect();
  if (capture.source) capture.source.disconnect();
  if (capture.stream) capture.stream.getTracks().forEach((track) => track.stop());
  if (capture.ctx) await capture.ctx.close().catch(() => {{}});
}}

async function startReferenceRecording() {{
  refRecorderChunks = [];
  // Для записи референса собираем Float32 чанки (потом энкодим в WAV вручную)
  const stream = await requestMicrophoneStream();
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const source = ctx.createMediaStreamSource(stream);
  const processorNode = ctx.createScriptProcessor(4096, 1, 1);

  processorNode.onaudioprocess = (event) => {{
    const data = new Float32Array(event.inputBuffer.getChannelData(0));
    refRecorderChunks.push(data);
  }};

  source.connect(processorNode);
  processorNode.connect(ctx.destination);

  refRecorderCapture = {{ stream, ctx, source, processor: processorNode }};
  refRecorderSampleRate = ctx.sampleRate;

  refRecorderActive = true;
  setupRecordBtn.classList.add("recording");
  setupRecordBtn.textContent = "Остановить запись";
  setRecorderStatus(
    "Идёт запись. Прочитайте три предложения и нажмите кнопку ещё раз, когда закончите.",
    "live"
  );
}}

async function stopReferenceRecording() {{
  refRecorderActive = false;
  setupRecordBtn.classList.remove("recording");
  setupRecordBtn.textContent = "Сохраняем запись...";
  setupRecordBtn.disabled = true;
  setRecorderStatus("Сохраняем WAV и подготавливаем референсный голос...", "");

  const merged = mergeFloat32Chunks(refRecorderChunks);
  await cleanupReferenceRecorder();

  if (!merged.length || merged.length < refRecorderSampleRate * 2) {{
    setupRecordBtn.disabled = false;
    setupRecordBtn.textContent = "Начать запись";
    setRecorderStatus("Запись получилась слишком короткой. Повторите и прочитайте все три предложения.", "error");
    return;
  }}

  runtimeRefAudioB64 = arrayBufferToBase64(encodeWav(merged, refRecorderSampleRate));
  runtimeRefLabel = "Записанный референс";
  updateReferenceBadge();
  setupRecordBtn.disabled = false;
  setupRecordBtn.textContent = "Начать запись";
  setRecorderStatus("Референс сохранён. Открываем основной экран.", "live");
  finishSetup("Референс записан в браузере и будет использован вместо загруженного файла.");
}}

async function toggleReferenceRecording() {{
  try {{
    if (refRecorderActive) await stopReferenceRecording();
    else await startReferenceRecording();
  }} catch (error) {{
    await cleanupReferenceRecorder();
    refRecorderActive = false;
    setupRecordBtn.classList.remove("recording");
    setupRecordBtn.disabled = false;
    setupRecordBtn.textContent = "Начать запись";
    const reason = error && (error.message || error.name) ? ` (${{error.message || error.name}})` : "";
    setRecorderStatus(`Не удалось получить доступ к микрофону для записи референса${{reason}}`, "error");
  }}
}}

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

/* ─────────────────────────────────────────────
   ИСПРАВЛЕНИЕ 4: streamFile — Float32 → PCM16 перед отправкой
───────────────────────────────────────────── */
async function streamFile() {{
  setModeLabel("Файл");
  setStatus(
    "Декодируем файл",
    "",
    "Аудиофайл разбивается на небольшие чанки и отправляется в backend с той же логикой, что и микрофон."
  );

  const bytes = b64ToBytes(AUDIO_B64);
  const decCtx = new (window.AudioContext || window.webkitAudioContext)();
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
    if (ws?.readyState === WebSocket.OPEN) {{
      // Конвертируем Float32 → Int16 PCM перед отправкой
      const pcm16Buffer = floatTo16BitPCM(chunk);
      ws.send(pcm16Buffer);
    }}
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

/* ─────────────────────────────────────────────
   ИСПРАВЛЕНИЕ 5: startMic — onChunk теперь получает ArrayBuffer (PCM16),
   буферизация Float32 больше не нужна, отправляем напрямую
───────────────────────────────────────────── */
async function startMic() {{
  setModeLabel("Микрофон");

  const capture = await openMicrophoneCapture((pcm16Buffer) => {{
    // pcm16Buffer — уже готовый ArrayBuffer с Int16 PCM
    if (ws?.readyState === WebSocket.OPEN) ws.send(pcm16Buffer);
  }});

  micStream = capture.stream;
  inputCtx = capture.ctx;
  micSrc = capture.source;
  processor = capture.processor;
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
  if (!setupComplete) {{
    setStatus("Сначала выберите голос", "error", "Нужно завершить шаг выбора референсного голоса перед основной записью.");
    return;
  }}
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
      ref_audio: runtimeRefAudioB64 || "",
      ref_text: REF_TEXT || "",
      lang: REF_LANG || "Russian",
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
      // Объединяем stable + pending — весь входящий текст сразу коммитится как основной
      const fullText = [payload.stable, payload.pending].filter(Boolean).join(" ").trim();
      setTranscript(fullText, "");
      updateStats(fullText, "", payload.speed_logs || "");
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

chooseUploadBtn.addEventListener("click", chooseUploadedReference);
chooseRecordBtn.addEventListener("click", showSetupRecorder);
setupBackBtn.addEventListener("click", async () => {{
  if (refRecorderActive) {{
    refRecorderActive = false;
    await cleanupReferenceRecorder();
  }}
  setupRecordBtn.classList.remove("recording");
  setupRecordBtn.disabled = false;
  setupRecordBtn.textContent = "Начать запись";
  showSetupChoice();
}});
setupRecordBtn.addEventListener("click", toggleReferenceRecording);

setModeLabel(HAS_FILE ? "Файл" : "Микрофон");
updateReferenceBadge();
setVisibility(setupScreenEl, false);
setVisibility(mainAppEl, true);
setStatus("Готово к запуску", "", "Референсный голос уже подготовлен и сохранён. Можно начинать перевод.");
if (animationFrame === null) animationFrame = window.requestAnimationFrame(animateVoiceLine);
</script>
</body>
</html>
""",
    height=1500,
    scrolling=True,
)