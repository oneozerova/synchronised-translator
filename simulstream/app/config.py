"""
Configuration – reads from environment variables or .env file.
"""
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ── Server ────────────────────────────────────────────────────────────
    host: str = Field("0.0.0.0", description="FastAPI bind address")
    port: int = Field(8000, description="FastAPI port")
    cors_origins: List[str] = Field(["*"], description="Allowed CORS origins")

    # ── Whisper TCP server ────────────────────────────────────────────────
    whisper_host: str = Field("127.0.0.1", description="Whisper server hostname")
    whisper_port: int = Field(43001, description="Whisper server TCP port")
    whisper_connect_timeout: float = Field(10.0, description="Seconds to wait for Whisper connection")

    # ── Translate TCP server ──────────────────────────────────────────────
    translate_host: str = Field("127.0.0.1", description="Translate server hostname")
    translate_port: int = Field(43002, description="Translate server TCP port")
    translate_connect_timeout: float = Field(10.0, description="Seconds to wait for Translate connection")

    # ── SimulStreaming subprocess args ────────────────────────────────────
    # Paths to the SimulStreaming entry points
    whisper_server_script: str = Field(
        "../SimulStreaming/simulstreaming_whisper_server.py",
        description="Path to simulstreaming_whisper_server.py",
    )
    translate_server_script: str = Field(
        "../SimulStreaming/simulstreaming_translate_server.py",
        description="Path to simulstreaming_translate_server.py",
    )

    # Whisper model
    whisper_model_path: str = Field(
        "large-v3",
        description="Whisper model name or path to .pt file",
    )
    whisper_language: str = Field("auto", description="Default source language for Whisper")
    whisper_task: str = Field("transcribe", description="'transcribe' or 'translate'")
    whisper_beams: int = Field(5, description="Beam search width")
    whisper_vac: bool = Field(True, description="Use Voice Activity Controller")
    whisper_min_chunk_size: float = Field(1.0, description="Min audio chunk size in seconds")
    whisper_frame_threshold: int = Field(8, description="AlignAtt frame threshold")

    manage_subprocesses: bool = Field(
        True,
        description="...",
    )
    # Добавь сразу после:
    manage_translate: bool = Field(
        True,
        description="If False, skip launching/waiting for the Translate server.",
    )

    # LLM translation model
    translate_model_dir: str = Field(
        "ct2_EuroLLM-9B-Instruct",
        description="CTranslate2 model directory",
    )
    translate_tokenizer_dir: str = Field(
        "EuroLLM-9B-Instruct",
        description="HuggingFace tokenizer directory",
    )
    translate_min_chunk_size: int = Field(
        3, description="Min words per LLM update"
    )

    # ── Subprocess management ─────────────────────────────────────────────
    manage_subprocesses: bool = Field(
        True,
        description="If True, backend starts/stops Whisper & Translate servers automatically. "
                    "Set False if you start them manually.",
    )
    subprocess_startup_grace: float = Field(
        30.0, description="Seconds to wait for subprocesses to become ready"
    )

    # ── Audio ─────────────────────────────────────────────────────────────
    audio_sample_rate: int = Field(16000, description="Expected PCM sample rate")
    audio_chunk_bytes: int = Field(
        3200,
        description="How many bytes to buffer before flushing to Whisper (100 ms @ 16kHz S16_LE)",
    )

    # ── Session ───────────────────────────────────────────────────────────
    session_idle_timeout: float = Field(
        120.0, description="Seconds before an idle session is closed"
    )


settings = Settings()
