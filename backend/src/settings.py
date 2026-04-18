from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    stt_port: int = 8001

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
