# synchronised-translator

Прототип пайплайна синхронного перевода речи:

`speech → text → translation → text-to-speech`

В репозитории уже есть отдельные модули для распознавания речи, потокового перевода и потокового синтеза речи. Сейчас это набор независимых сервисов и демо-скриптов, а не один собранный production-ready backend.

## Что есть в репозитории

- `speech2text/` — потоковое распознавание английской речи через `faster-whisper` и VAD.
- `translator/` — переводчик на базе YandexGPT с контекстным окном для перевода входящих чанков.
- `tts/` — потоковый TTS по websocket, ориентированный на Qwen3-TTS.
- `backend/` — пока заглушка, полноценной orchestration-логики здесь еще нет.

## Структура

- [speech2text/server.py](/Users/dmitrijozerov/synchronised-translator/speech2text/server.py:1) — FastAPI websocket-сервис STT.
- [speech2text/app.py](/Users/dmitrijozerov/synchronised-translator/speech2text/app.py:1) — Streamlit-демо для микрофона или аудиофайла.
- [speech2text/VAD_processing.py](/Users/dmitrijozerov/synchronised-translator/speech2text/VAD_processing.py:1) — обработка VAD на базе Silero.
- [translator/yandexgpt.py](/Users/dmitrijozerov/synchronised-translator/translator/yandexgpt.py:1) — сессионный переводчик с накоплением исходного контекста.
- [tts/tts.py](/Users/dmitrijozerov/synchronised-translator/tts/tts.py:1) — FastAPI websocket-сервис потокового TTS.
- [tts/tts_front.html](/Users/dmitrijozerov/synchronised-translator/tts/tts_front.html:1) — браузерное демо для TTS.

## Требования

- Python 3.10+
- `ffmpeg`
- Для TTS и части VAD/STT желательно наличие CUDA/GPU, но часть кода умеет падать обратно на CPU
- Виртуальное окружение Python

Базовые зависимости описаны в [requirements.txt](/Users/dmitrijozerov/synchronised-translator/requirements.txt:1), но для `tts/tts.py` понадобится еще библиотека `faster_qwen3_tts`, которой сейчас в `requirements.txt` нет.

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

Если нужен `ffmpeg` на macOS:

```bash
brew install ffmpeg
```

## Переменные окружения

Для переводчика нужен API-ключ Yandex Cloud:

```bash
export YANDEX_API_KEY="your_key_here"
export YANDEX_FOLDER_ID="your_folder_id"
```

`YANDEX_FOLDER_ID` необязателен, если подходит дефолт из кода, но для сервиса лучше передавать его явно.

## Запуск модулей

### 1. STT websocket-сервис

Из корня репозитория:

```bash
cd speech2text
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
```

Что делает сервис:

- принимает аудио-чанки `float32` по websocket на `/ws`
- прогоняет их через VAD
- распознает речь через `faster-whisper`
- возвращает JSON с полями `stable`, `pending`, `chars`, `speed_logs`

Проверка health:

```bash
curl http://localhost:8001/health
```

### 2. STT демо-интерфейс

В отдельном терминале:

```bash
streamlit run speech2text/app.py
```

По умолчанию фронт подключается к `ws://localhost:8001/ws`.

### 3. Переводчик

Сейчас переводчик реализован как Python-модуль с классом `TranslationSession`, который:

- принимает новые чанки текста
- накапливает исходный контекст
- обрезает контекст до заданного окна токенов
- отправляет в модель `context_window + current_chunk`
- возвращает перевод только нового фрагмента

Запуск демо:

```bash
python translator/yandexgpt.py
```

Ключевые параметры в [translator/yandexgpt.py](/Users/dmitrijozerov/synchronised-translator/translator/yandexgpt.py:1):

- `source_language`
- `target_language`
- `max_context_tokens`
- `SYSTEM_PROMPT`

Сейчас `TranslationSession` хранит весь исходный текст в памяти и использует простую токенизацию через `split()`. Для микросервиса это нормальная стартовая точка, но не финальная реализация.

### 4. Потоковый TTS

Запуск сервиса:

```bash
python tts/tts.py
```

Или:

```bash
uvicorn tts.tts:app --host 0.0.0.0 --port 8000 --reload
```

Сервис:

- слушает websocket ` /api/generate`
- принимает событие `start` с reference audio и reference text
- затем принимает события `chunk` с текстом
- возвращает JSON-события статуса и бинарные PCM-аудио-фреймы

Проверка health:

```bash
curl http://localhost:8000/health
```

Фронтовое демо лежит в [tts/tts_front.html](/Users/dmitrijozerov/synchronised-translator/tts/tts_front.html:1), но там сейчас захардкожен внешний `WS_URL`, так что для локального запуска его нужно заменить на локальный адрес сервиса.

## Как это должно собираться в пайплайн

Планируемый поток данных такой:

1. Клиент отправляет аудио чанками в `speech2text`.
2. `speech2text` возвращает стабилизированный текст.
3. Новый текстовый чанк отправляется в `translator`.
4. `translator` переводит только текущий чанк с учетом предыдущего контекста.
5. Переведенный текст чанками уходит в `tts`.
6. `tts` начинает воспроизведение, не дожидаясь завершения всей фразы.

## Текущее состояние и ограничения

- Корневой `backend/` пока не реализует склейку всех этапов.
- `Dockerfile` сейчас не соответствует текущей структуре проекта и требует актуализации.
- `requirements.txt` покрывает не все зависимости для TTS.
- В `speech2text` и `tts` пока есть хардкод параметров модели, портов и путей.
- Переводчик пока оформлен как локальный модуль, а не как отдельный HTTP/WebSocket-микросервис.

## Ближайшие шаги

- вынести конфигурацию сервисов в `.env`
- добавить единый backend-оркестратор
- обернуть `translator` в FastAPI/WebSocket сервис
- привести `Dockerfile` и зависимости к реальному состоянию репозитория
- добавить инструкции по локальному и GPU-развертыванию


`make build` - собрать все образы

`make up` - запустить сервисы

`make down` - убить сервисы

`make restart` - перезапустить сервисы

Настройка портов производится в `.env`