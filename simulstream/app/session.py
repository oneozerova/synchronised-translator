"""
TranslationSession
==================
Manages one WebSocket ↔ TCP pipeline for a single client.

Data flow:
  WebSocket binary frames  ──► audio_queue
                                    │
                                    ▼
                            [_audio_sender coroutine]
                                    │  raw PCM bytes → Whisper TCP socket
                                    ▼
                            [_whisper_reader coroutine]
                                    │  JSONL lines → translate_queue  (cascade mode)
                                    │  JSONL lines → WebSocket        (whisper-only mode)
                                    ▼
                            [_translate_reader coroutine]
                                    │  JSONL lines → result_queue
                                    ▼
                            [_result_sender coroutine]
                                    │  JSON → WebSocket text frames
"""

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import WebSocket

from app.config import settings
from app.models import SessionInfo, TranslationResult, WhisperChunk, TranslationChunk
from app.pipeline import PipelineManager

logger = logging.getLogger("simul.session")


class TranslationSession:
    def __init__(
        self,
        session_id: str,
        websocket: WebSocket,
        pipeline_manager: PipelineManager,
        src_lang: str = "en",
        tgt_lang: str = "de",
        task: str = "translate",
        mode: str = "cascade",  # "whisper" | "cascade"
    ):
        self.session_id = session_id
        self.websocket = websocket
        self.pipeline = pipeline_manager
        self.src_lang = src_lang
        self.tgt_lang = tgt_lang
        self.task = task
        self.mode = mode

        self.started_at = time.time()
        self.audio_bytes_received = 0
        self.messages_sent = 0

        # Internal queues
        self._audio_q: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=64)
        self._translate_q: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=256)
        self._result_q: asyncio.Queue[Optional[dict]] = asyncio.Queue(maxsize=256)

        # TCP connections
        self._whisper_reader: Optional[asyncio.StreamReader] = None
        self._whisper_writer: Optional[asyncio.StreamWriter] = None
        self._translate_reader: Optional[asyncio.StreamReader] = None
        self._translate_writer: Optional[asyncio.StreamWriter] = None

        self._closed = False
        self._tasks: list[asyncio.Task] = []

    # ── Public API ───────────────────────────────────────────────────────

    async def run(self):
        """Open connections, start coroutines, wait until session ends."""
        await self._connect()
        await self._send_info("Connected. Ready to receive audio.")

        coros = [
            self._ws_receiver(),
            self._audio_sender(),
            self._whisper_reader_loop(),
            self._result_sender(),
        ]
        if self.mode == "cascade":
            coros.append(self._translate_sender())
            coros.append(self._translate_reader_loop())

        self._tasks = [asyncio.create_task(c) for c in coros]

        done, pending = await asyncio.wait(
            self._tasks, return_when=asyncio.FIRST_EXCEPTION
        )
        # Cancel remaining tasks
        for t in pending:
            t.cancel()
        for t in done:
            if not t.cancelled() and t.exception():
                logger.warning("Task raised: %s", t.exception())

    async def close(self):
        if self._closed:
            return
        self._closed = True
        for t in self._tasks:
            t.cancel()
        for writer in [self._whisper_writer, self._translate_writer]:
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

    def info(self) -> SessionInfo:
        return SessionInfo(
            session_id=self.session_id,
            src_lang=self.src_lang,
            tgt_lang=self.tgt_lang,
            task=self.task,
            mode=self.mode,
            started_at=self.started_at,
            audio_bytes_received=self.audio_bytes_received,
            messages_sent=self.messages_sent,
        )

    # ── Internal: connection setup ───────────────────────────────────────

    async def _connect(self):
        self._whisper_reader, self._whisper_writer = (
            await self.pipeline.open_whisper_connection()
        )
        logger.info("[%s] Connected to Whisper server", self.session_id)

        if self.mode == "cascade":
            self._translate_reader, self._translate_writer = (
                await self.pipeline.open_translate_connection()
            )
            logger.info("[%s] Connected to Translate server", self.session_id)

    # ── Coroutines ───────────────────────────────────────────────────────

    async def _ws_receiver(self):
        """Read WebSocket frames and put audio bytes into the audio queue."""
        try:
            while not self._closed:
                msg = await asyncio.wait_for(
                    self.websocket.receive(),
                    timeout=settings.session_idle_timeout,
                )

                if msg["type"] == "websocket.disconnect":
                    break

                if msg.get("bytes"):
                    chunk = msg["bytes"]
                    self.audio_bytes_received += len(chunk)
                    await self._audio_q.put(chunk)

                elif msg.get("text"):
                    text = msg["text"].strip()
                    if text == "DONE":
                        logger.info("[%s] DONE received, draining …", self.session_id)
                        await self._audio_q.put(None)  # sentinel
                        break
        except asyncio.TimeoutError:
            logger.warning("[%s] Idle timeout", self.session_id)
        finally:
            await self._audio_q.put(None)

    async def _audio_sender(self):
        """Drain audio queue and write raw PCM to Whisper TCP socket."""
        try:
            while not self._closed:
                chunk = await self._audio_q.get()
                if chunk is None:
                    # EOF – close write side so Whisper server knows stream ended
                    if self._whisper_writer and not self._whisper_writer.is_closing():
                        self._whisper_writer.write_eof()
                        await self._whisper_writer.drain()
                    break
                self._whisper_writer.write(chunk)
                await self._whisper_writer.drain()
        except Exception as exc:
            logger.warning("[%s] _audio_sender error: %s", self.session_id, exc)

    async def _whisper_reader_loop(self):
        """
        Read JSONL lines from Whisper TCP socket.
        In 'whisper' mode  → forward results to client directly.
        In 'cascade' mode  → push lines to translate queue.
        """
        try:
            while not self._closed:
                line = await self._whisper_reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                logger.debug("[%s] Whisper out: %s", self.session_id, text[:120])

                if self.mode == "cascade":
                    await self._translate_q.put(text)
                else:
                    # whisper-only: parse and emit to client
                    try:
                        raw = json.loads(text)
                        chunk = WhisperChunk(**raw)
                        result = TranslationResult(
                            type="final" if chunk.is_final else "partial",
                            session_id=self.session_id,
                            src_text=chunk.text or "",
                            start=chunk.start,
                            end=chunk.end,
                            is_final=chunk.is_final,
                            emission_time=chunk.emission_time,
                        )
                        await self._result_q.put(result.model_dump())
                    except Exception as exc:
                        logger.warning("[%s] Failed to parse Whisper JSON: %s", self.session_id, exc)
        except Exception as exc:
            logger.warning("[%s] _whisper_reader_loop error: %s", self.session_id, exc)
        finally:
            await self._translate_q.put(None)
            await self._result_q.put(None)

    async def _translate_sender(self):
        """Forward Whisper JSONL lines from queue to Translate TCP socket."""
        try:
            while not self._closed:
                line = await self._translate_q.get()
                if line is None:
                    # EOF – notify translate server
                    if self._translate_writer and not self._translate_writer.is_closing():
                        try:
                            self._translate_writer.write_eof()
                            await self._translate_writer.drain()
                        except Exception:
                            pass
                    break
                self._translate_writer.write((line + "\n").encode())
                await self._translate_writer.drain()
        except Exception as exc:
            logger.warning("[%s] _translate_sender error: %s", self.session_id, exc)

    async def _translate_reader_loop(self):
        """
        Read JSONL lines from Translate TCP socket.
        Build a unified TranslationResult combining Whisper src_text + LLM tgt_text.
        """
        # We keep the last confirmed src_text snippet from Whisper to attach to results.
        last_src_text: str = ""

        # Track running confirmed translation
        confirmed_text: str = ""

        try:
            while not self._closed:
                line = await self._translate_reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                logger.debug("[%s] Translate out: %s", self.session_id, text[:120])

                try:
                    raw = json.loads(text)
                    chunk = TranslationChunk(**raw)

                    if chunk.text:
                        confirmed_text += chunk.text

                    result_type = "final" if chunk.is_final else "partial"
                    result = TranslationResult(
                        type=result_type,
                        session_id=self.session_id,
                        src_text=last_src_text,
                        tgt_text=confirmed_text,
                        unconfirmed=chunk.unconfirmed_text,
                        end=chunk.end,
                        is_final=chunk.is_final,
                        emission_time=chunk.emission_time,
                    )

                    if chunk.is_final:
                        # Reset accumulators on voiced segment boundary
                        last_src_text = ""
                        confirmed_text = ""

                    await self._result_q.put(result.model_dump())
                except Exception as exc:
                    logger.warning("[%s] Failed to parse Translate JSON: %s", self.session_id, exc)
        except Exception as exc:
            logger.warning("[%s] _translate_reader_loop error: %s", self.session_id, exc)
        finally:
            await self._result_q.put(None)

    async def _result_sender(self):
        """Drain result queue and send JSON to WebSocket client."""
        try:
            while not self._closed:
                result = await self._result_q.get()
                if result is None:
                    break
                await self.websocket.send_json(result)
                self.messages_sent += 1
        except Exception as exc:
            logger.warning("[%s] _result_sender error: %s", self.session_id, exc)

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _send_info(self, message: str):
        try:
            await self.websocket.send_json(
                {"type": "info", "session_id": self.session_id, "detail": message}
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Session Registry
# ---------------------------------------------------------------------------


class SessionRegistry:
    def __init__(self):
        self._sessions: dict[str, TranslationSession] = {}

    def add(self, session: TranslationSession):
        self._sessions[session.session_id] = session

    def get(self, session_id: str) -> Optional[TranslationSession]:
        return self._sessions.get(session_id)

    def remove(self, session_id: str):
        self._sessions.pop(session_id, None)

    def count(self) -> int:
        return len(self._sessions)

    def list_sessions(self) -> list:
        return [s.info() for s in self._sessions.values()]

    async def close_all(self):
        for session in list(self._sessions.values()):
            await session.close()
        self._sessions.clear()
