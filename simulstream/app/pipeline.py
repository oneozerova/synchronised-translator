"""
PipelineManager
===============
Optionally starts simulstreaming_whisper_server.py and
simulstreaming_translate_server.py as subprocesses, and provides
helpers to open async TCP connections to them.
"""

import asyncio
import logging
import subprocess
import sys
from typing import Optional, Tuple

from app.config import settings

logger = logging.getLogger("simul.pipeline")


class PipelineManager:
    """Manages (optionally) the Whisper and Translate TCP server subprocesses."""

    def __init__(self):
        self._whisper_proc: Optional[subprocess.Popen] = None
        self._translate_proc: Optional[subprocess.Popen] = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self):
        if not settings.manage_subprocesses:
            logger.info("Subprocess management disabled – assuming servers are already running.")
            return

        logger.info("Launching Whisper server …")
        self._whisper_proc = self._spawn_whisper()

        logger.info("Launching Translate server …")
        self._translate_proc = self._spawn_translate()

        logger.info(
            "Waiting up to %.0fs for servers to become ready …",
            settings.subprocess_startup_grace,
        )
        await self._wait_ready(settings.subprocess_startup_grace)

    async def stop(self):
        for name, proc in [
            ("whisper", self._whisper_proc),
            ("translate", self._translate_proc),
        ]:
            if proc is not None:
                logger.info("Terminating %s server (pid=%s) …", name, proc.pid)
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    # ── Subprocess spawning ──────────────────────────────────────────────

    def _spawn_whisper(self) -> subprocess.Popen:
        cmd = [
            sys.executable,
            settings.whisper_server_script,
            "--host", settings.whisper_host,
            "--port", str(settings.whisper_port),
            "--model_path", settings.whisper_model_path,
            "--lan", settings.whisper_language,
            "--task", settings.whisper_task,
            "--beams", str(settings.whisper_beams),
            "--min-chunk-size", str(settings.whisper_min_chunk_size),
            "--frame_threshold", str(settings.whisper_frame_threshold),
        ]
        if settings.whisper_vac:
            cmd.append("--vac")

        logger.debug("Whisper cmd: %s", " ".join(cmd))
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _spawn_translate(self) -> subprocess.Popen:
        cmd = [
            sys.executable,
            settings.translate_server_script,
            "--host", settings.translate_host,
            "--port", str(settings.translate_port),
            "--model-dir", settings.translate_model_dir,
            "--tokenizer-dir", settings.translate_tokenizer_dir,
            "--min-chunk-size", str(settings.translate_min_chunk_size),
        ]
        logger.debug("Translate cmd: %s", " ".join(cmd))
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # ── Readiness probes ─────────────────────────────────────────────────

    async def _wait_ready(self, timeout: float):
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            w = await self.whisper_ready()
            t = await self.translate_ready()
            if w and t:
                logger.info("Both servers ready ✓")
                return
            await asyncio.sleep(1.0)
        raise RuntimeError(
            "Timed out waiting for SimulStreaming servers to become ready. "
            "Check that the model files exist and GPU memory is sufficient."
        )

    async def _tcp_probe(self, host: str, port: int, timeout: float = 1.0) -> bool:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    async def whisper_ready(self) -> bool:
        return await self._tcp_probe(settings.whisper_host, settings.whisper_port)

    async def translate_ready(self) -> bool:
        return await self._tcp_probe(settings.translate_host, settings.translate_port)

    # ── Connection factory ───────────────────────────────────────────────

    async def open_whisper_connection(
        self,
    ) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open an async TCP connection to the Whisper server."""
        return await asyncio.wait_for(
            asyncio.open_connection(settings.whisper_host, settings.whisper_port),
            timeout=settings.whisper_connect_timeout,
        )

    async def open_translate_connection(
        self,
    ) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open an async TCP connection to the Translate server."""
        return await asyncio.wait_for(
            asyncio.open_connection(settings.translate_host, settings.translate_port),
            timeout=settings.translate_connect_timeout,
        )
