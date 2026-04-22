# synchronised-translator

Репозиторий рефакторирован в архитектуру с единым backend-оркестратором:

`audio -> STT -> Translator -> TTS -> audio`

## Структура

- `frontend/app.py` — основной Streamlit frontend (микрофон/файл, reference audio/text, live translation, live TTS playback)
- `backend/src/main.py` — websocket orchestrator `/ws`, связывает `speech-to-text`, `translator`, `tts`
- `speech-to-text/src/main.py` — streaming STT сервис (`/ws`)
- `translator/src/main.py` — websocket переводчик (`/ws`)
- `tts/tts.py` — streaming TTS сервис (`/api/generate`)
- `app.py` и `speech-to-text/src/app.py` — compatibility launchers, перенаправляют на `frontend/app.py`

## Протокол frontend -> backend

1. После подключения frontend отправляет JSON:
   - `event: "session_start"`
   - `ref_audio` (base64 wav, опционально)
   - `ref_text` (опционально)
   - `lang` (опционально, по умолчанию `Russian`)
2. Дальше frontend шлет бинарные `float32` аудио чанки.
3. Для завершения сессии frontend отправляет `event: "session_end"`.

Backend возвращает:

- JSON с `event: "translation"` и `stable/pending`
- JSON/bytes события TTS (`started/chunk_begin/chunk_end/done` и PCM binary frames)

## Переменные окружения backend

- `STT_HOST` / `STT_PORT` (default: `127.0.0.1:8001`)
- `TRANSLATOR_HOST` / `TRANSLATOR_PORT` (default: `127.0.0.1:8002`)
- `TTS_HOST` / `TTS_PORT` (default: `127.0.0.1:8003`)
- `TTS_PATH` (default: `/api/generate`)
- `TTS_DEFAULT_LANG` (default: `Russian`)

## Локальный запуск

В разных терминалах:

```bash
# 1) STT
cd speech-to-text
uv run uvicorn src.main:app --host 0.0.0.0 --port 8001
```

```bash
# 2) Translator
cd translator
uv run uvicorn src.main:app --host 0.0.0.0 --port 8002
```

```bash
# 3) TTS
cd tts
python tts.py
```

```bash
# 4) Backend orchestrator
cd backend
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000
```

```bash
# 5) Frontend
streamlit run frontend/app.py
```

## Удалено как неиспользуемое

- `test-front/front_stt.html`
- `test-front/index.html`

Эти демо-файлы больше не участвуют в основном потоке приложения.
