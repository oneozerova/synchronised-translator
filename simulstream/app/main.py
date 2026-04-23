"""
SimulStreaming Backend Service
==============================
FastAPI + WebSocket backend for real-time streaming speech-to-text translation.

Architecture:
  Browser/Client
      │  WebSocket (binary PCM audio + JSON results)
      ▼
  FastAPI Backend  (this file)
      │  TCP socket (raw PCM bytes)       TCP socket (JSONL)
      ├──────────────────────────────► Whisper Server ──────────────────────────► Translate Server
      │                                  (ASR)                                      (LLM MT)
      └◄─────────────────────────────────────────────────────────────────────────────────────────
         JSON results streamed back to client via WebSocket
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.config import settings
from app.pipeline import PipelineManager
from app.session import TranslationSession, SessionRegistry
from app.models import (
    HealthResponse,
    SessionInfo,
    SessionListResponse,
    TranslationResult,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
logger = logging.getLogger("simul.main")

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
pipeline_manager = PipelineManager()
session_registry = SessionRegistry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting SimulStreaming backend …")
    await pipeline_manager.start()
    yield
    logger.info("Shutting down SimulStreaming backend …")
    await session_registry.close_all()
    await pipeline_manager.stop()


app = FastAPI(
    title="SimulStreaming Backend",
    description="Real-time streaming speech-to-text + LLM translation via WebSocket",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health_check():
    """Liveness + readiness probe."""
    whisper_ok = await pipeline_manager.whisper_ready()
    translate_ok = await pipeline_manager.translate_ready()
    return HealthResponse(
        status="ok" if (whisper_ok and translate_ok) else "degraded",
        whisper_server=whisper_ok,
        translate_server=translate_ok,
        active_sessions=session_registry.count(),
    )


@app.get("/sessions", response_model=SessionListResponse, tags=["sessions"])
async def list_sessions():
    return SessionListResponse(sessions=session_registry.list_sessions())


@app.get("/sessions/{session_id}", response_model=SessionInfo, tags=["sessions"])
async def get_session(session_id: str):
    session = session_registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.info()


@app.delete("/sessions/{session_id}", tags=["sessions"])
async def close_session(session_id: str):
    session = session_registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    await session.close()
    session_registry.remove(session_id)
    return {"detail": "closed"}


@app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
async def demo_page():
    """Minimal browser demo page."""
    with open("app/static/demo.html") as f:
        return f.read()


# ---------------------------------------------------------------------------
# WebSocket – main streaming endpoint
# ---------------------------------------------------------------------------


@app.websocket("/ws/translate")
async def ws_translate(
    websocket: WebSocket,
    src_lang: str = Query("en", description="Source language code (e.g. 'en', 'de', 'cs')"),
    tgt_lang: str = Query("de", description="Target language code (e.g. 'de', 'fr', 'ru')"),
    task: str = Query("translate", description="'transcribe' or 'translate'"),
    mode: str = Query("cascade", description="'whisper' (ASR only) or 'cascade' (ASR + LLM MT)"),
):
    """
    WebSocket endpoint for real-time speech translation.

    Protocol
    --------
    Client → Server:
      - Binary frames: raw PCM audio (S16_LE, 16 kHz, mono)
      - Text frame "DONE": signal end of audio stream

    Server → Client (JSON text frames):
      {
        "type":           "partial" | "final" | "error" | "info",
        "session_id":     "<uuid>",
        "src_text":       "transcribed text (partial)",
        "tgt_text":       "translated text (confirmed)",
        "unconfirmed":    "translation in progress",
        "start":          0.0,          # audio offset (seconds)
        "end":            1.5,
        "is_final":       false,
        "emission_time":  1.234
      }
    """
    await websocket.accept()

    session_id = str(uuid.uuid4())
    logger.info("New WS connection: session=%s src=%s tgt=%s mode=%s", session_id, src_lang, tgt_lang, mode)

    session = TranslationSession(
        session_id=session_id,
        websocket=websocket,
        pipeline_manager=pipeline_manager,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        task=task,
        mode=mode,
    )
    session_registry.add(session)

    try:
        await session.run()
    except WebSocketDisconnect:
        logger.info("Client disconnected: session=%s", session_id)
    except Exception as exc:
        logger.exception("Session error: session=%s", session_id, exc_info=exc)
        try:
            await websocket.send_json({"type": "error", "detail": str(exc)})
        except Exception:
            pass
    finally:
        await session.close()
        session_registry.remove(session_id)
