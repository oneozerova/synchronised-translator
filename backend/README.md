# Backend

- **`src/main.py`** — единая точка WebSocket для фронта: STT и TTS по сети, переводчик **в процессе** при `USE_TRANSLATOR=true`.
- **`src/translator.py`** — экспортируемый **`TranslationSession`** (YandexGPT через OpenAI-совместимый API).
- **`src/settings.py`** — конфигурация из `.env`.

Отдельного микросервиса «translator» в репозитории нет.
