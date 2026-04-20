from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    stt_host: str = "wss://apollo2.ci.nsu.ru/i.kadilenko/proxy"
    stt_port: int = 8001
    tts_host: str = "wss://apollo2.ci.nsu.ru/i.kadilenko/proxy"
    tts_port: int = 8003
    tts_path: str = "api/generate"
    enable_tts: bool = False
    tts_required: bool = False

    yandex_api_key: str = ""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @property
    def stt_url(self) -> str:
        return f"{self.stt_host}/{self.stt_port}/ws"

    @property
    def tts_url(self) -> str:
        return f"{self.tts_host}/{self.tts_port}/{self.tts_path}"


settings = Settings()
