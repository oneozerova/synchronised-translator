from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    stt_host: str = "127.0.0.1"
    stt_port: int = 8001
    translator_host: str = "127.0.0.1"
    translator_port: int = 8002
    tts_host: str = "127.0.0.1"
    tts_port: int = 8003
    tts_path: str = "/api/generate"
    tts_default_lang: str = "Russian"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
