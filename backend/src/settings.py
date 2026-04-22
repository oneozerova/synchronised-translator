from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.urls import build_websocket_url


class Settings(BaseSettings):
    """
    STT/TTS — через apollo-прокси (или полные URL).
    Переводчик — внутри процесса backend (см. src.translator), без отдельного сервиса.
    """

    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    stt_url: str | None = None
    tts_url: str | None = None

    service_proxy_base: str = "wss://apollo2.ci.nsu.ru/i.kadilenko/proxy"
    stt_port: int = 8001
    stt_path: str = "/ws"
    tts_port: int = 8003
    tts_path: str = "/api/generate"

    use_translator: bool = False
    tts_default_lang: str = "Russian"

    yandex_api_key: str | None = Field(default=None, description="Или переменная YANDEX_API_KEY")
    yandex_folder_id: str = "b1gq32mi56gh15jmvblj"
    translator_source_language: str = "Russian"
    translator_target_language: str = "English"

    def stt_websocket_url(self) -> str:
        if self.stt_url:
            return self.stt_url
        return build_websocket_url(self.service_proxy_base, self.stt_port, self.stt_path)

    def tts_websocket_url(self) -> str:
        if self.tts_url:
            return self.tts_url
        return build_websocket_url(self.service_proxy_base, self.tts_port, self.tts_path)


settings = Settings()
