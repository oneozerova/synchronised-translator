"""
example_client.py
=================
Example Python client showing how to stream audio to the SimulStreaming backend
and receive real-time transcription + translation results.

Requirements:
    pip install websockets sounddevice numpy

Usage:
    # Stream from microphone:
    python example_client.py --mic

    # Stream from audio file:
    python example_client.py --file audio.wav --src en --tgt de

    # Transcription only (no translation):
    python example_client.py --mic --mode whisper --src de
"""

import argparse
import asyncio
import json
import struct
import sys
import wave
from typing import AsyncIterator

import numpy as np

try:
    import websockets
except ImportError:
    print("Run: pip install websockets")
    sys.exit(1)


# ── Config ────────────────────────────────────────────────────────────────────

BACKEND_URL = "ws://localhost:8000/ws/translate"
SAMPLE_RATE  = 16000
CHUNK_FRAMES = 3200   # 200 ms @ 16 kHz  (must match backend audio_chunk_bytes/2)


# ── Audio sources ─────────────────────────────────────────────────────────────

async def mic_source() -> AsyncIterator[bytes]:
    """Yield PCM chunks from the default microphone (S16_LE, 16 kHz, mono)."""
    try:
        import sounddevice as sd
    except ImportError:
        print("Run: pip install sounddevice")
        sys.exit(1)

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    def callback(indata, frames, time_info, status):
        pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
        loop.call_soon_threadsafe(queue.put_nowait, pcm)

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=CHUNK_FRAMES,
        callback=callback,
    ):
        print("🎙  Microphone open. Press Ctrl-C to stop.")
        try:
            while True:
                chunk = await queue.get()
                yield chunk
        except asyncio.CancelledError:
            pass


async def file_source(path: str, realtime: bool = True) -> AsyncIterator[bytes]:
    """
    Yield PCM chunks from a WAV file.
    If realtime=True, sleep between chunks to simulate real-time streaming.
    """
    with wave.open(path) as wf:
        assert wf.getsampwidth() == 2, "Expected 16-bit PCM"
        assert wf.getnchannels() == 1, "Expected mono"
        assert wf.getframerate() == SAMPLE_RATE, f"Expected {SAMPLE_RATE} Hz"

        chunk_duration = CHUNK_FRAMES / SAMPLE_RATE
        print(f"📂  Streaming file: {path}  (duration ~{wf.getnframes()/SAMPLE_RATE:.1f}s)")

        while True:
            data = wf.readframes(CHUNK_FRAMES)
            if not data:
                break
            yield data
            if realtime:
                await asyncio.sleep(chunk_duration)


# ── WebSocket client ──────────────────────────────────────────────────────────

async def stream_and_receive(
    audio_iter: AsyncIterator[bytes],
    src_lang: str = "en",
    tgt_lang: str = "de",
    mode: str = "cascade",
    verbose: bool = False,
):
    url = f"{BACKEND_URL}?src_lang={src_lang}&tgt_lang={tgt_lang}&mode={mode}"
    print(f"🔗  Connecting to {url}")

    async with websockets.connect(url, max_size=10 * 1024 * 1024) as ws:
        print("✅  Connected\n")

        # Sender coroutine
        async def send_audio():
            async for chunk in audio_iter:
                await ws.send(chunk)
            await ws.send("DONE")
            print("\n📨  DONE sent. Waiting for final results …")

        # Receiver coroutine
        async def receive_results():
            confirmed_src = ""
            confirmed_tgt = ""
            async for message in ws:
                msg = json.loads(message)
                t = msg.get("type")

                if t == "info":
                    print(f"ℹ  {msg.get('detail')}")
                    continue

                if t == "error":
                    print(f"❌  Error: {msg.get('detail')}")
                    continue

                src_text = msg.get("src_text", "")
                tgt_text = msg.get("tgt_text", "")
                unconfirmed = msg.get("unconfirmed", "")
                is_final = msg.get("is_final", False)
                end = msg.get("end")

                if verbose:
                    print(json.dumps(msg, ensure_ascii=False))
                    continue

                # Pretty display
                if src_text and src_text != confirmed_src:
                    confirmed_src = src_text
                    marker = "✔ " if is_final else "… "
                    ts = f"[{end:.1f}s]" if end else ""
                    print(f"\033[36m{marker}SRC {ts}: {src_text.strip()}\033[0m")

                if tgt_text and tgt_text != confirmed_tgt:
                    confirmed_tgt = tgt_text
                    marker = "✔ " if is_final else "… "
                    print(f"\033[32m{marker}TGT: {tgt_text.strip()}\033[0m")

                if unconfirmed and not tgt_text:
                    print(f"\033[90m  ~  {unconfirmed.strip()}\033[0m", end="\r")

        await asyncio.gather(send_audio(), receive_results())

    print("\n🏁  Done.")


# ── CLI ───────────────────────────────────────────────────────────────────────

async def main():
    global BACKEND_URL
    parser = argparse.ArgumentParser(description="SimulStreaming example client")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mic", action="store_true", help="Stream from microphone")
    group.add_argument("--file", metavar="WAV", help="Stream from WAV file (16kHz mono S16_LE)")
    parser.add_argument("--src", default="en", help="Source language (default: en)")
    parser.add_argument("--tgt", default="de", help="Target language (default: de)")
    parser.add_argument("--mode", choices=["cascade", "whisper"], default="cascade",
                        help="'cascade' = ASR+LLM, 'whisper' = ASR only")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print raw JSON")
    parser.add_argument("--url", default=BACKEND_URL, help="Backend WebSocket URL")
    args = parser.parse_args()

    BACKEND_URL = args.url

    if args.mic:
        source = mic_source()
    else:
        source = file_source(args.file)

    try:
        await stream_and_receive(source, args.src, args.tgt, args.mode, args.verbose)
    except KeyboardInterrupt:
        print("\n⚡  Interrupted.")


if __name__ == "__main__":
    asyncio.run(main())
