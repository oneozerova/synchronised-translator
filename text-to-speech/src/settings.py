"""Конфигурация сервиса text-to-speech (Qwen3-TTS)."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = Field(default="0.0.0.0", description="Слушать на этом адресе")
    port: int = Field(default=8003, description="Порт (совпадает с proxy/8003 на apollo)")

    default_ref_wav: Path = Field(
        default=_PACKAGE_ROOT / "resources" / "my_voice.wav",
        description="Референс по умолчанию, если ref_audio пустой",
    )


settings = Settings()
