from typing import List, Optional
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    whisper_server: bool
    translate_server: bool
    active_sessions: int


class SessionInfo(BaseModel):
    session_id: str
    src_lang: str
    tgt_lang: str
    task: str
    mode: str
    started_at: float
    audio_bytes_received: int
    messages_sent: int


class SessionListResponse(BaseModel):
    sessions: List[SessionInfo]


class WhisperWord(BaseModel):
    start: float
    end: float
    text: str


class WhisperChunk(BaseModel):
    """Parsed output from simulstreaming_whisper_server."""
    start: Optional[float] = None
    end: Optional[float] = None
    text: Optional[str] = None
    words: Optional[List[WhisperWord]] = None
    is_final: bool = False
    emission_time: Optional[float] = None


class TranslationChunk(BaseModel):
    """Parsed output from simulstreaming_translate_server."""
    emission_time: Optional[float] = None
    end: Optional[float] = None
    status: Optional[str] = None   # "INCOMPLETE" | "COMPLETE"
    text: str = ""
    unconfirmed_text: str = ""
    is_final: bool = False


class TranslationResult(BaseModel):
    """What we send back to the WebSocket client."""
    type: str                          # "partial" | "final" | "error" | "info"
    session_id: str
    src_text: Optional[str] = None
    tgt_text: Optional[str] = None
    unconfirmed: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None
    is_final: bool = False
    emission_time: Optional[float] = None
