# ws_to_tcp_proxy.py
# Запуск: python ws_to_tcp_proxy.py
# Затем: arecord -f S16_LE -c1 -r 16000 -t raw | nc localhost 43001
# Или браузер → ws://localhost:8010/ws → TCP → localhost:43001

import asyncio
import websockets

WHISPER_HOST = "127.0.0.1"
WHISPER_PORT = 43001
WS_PORT      = 8010

async def handle(ws):
    print("Client connected")
    reader, writer = await asyncio.open_connection(WHISPER_HOST, WHISPER_PORT)

    async def audio_to_tcp():
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    writer.write(msg)
                    await writer.drain()
                elif isinstance(msg, str) and msg.strip() == "DONE":
                    break
        finally:
            writer.write_eof()

    async def tcp_to_ws():
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                await ws.send(line.decode("utf-8", errors="replace").strip())
        except Exception:
            pass

    await asyncio.gather(audio_to_tcp(), tcp_to_ws())
    writer.close()
    print("Client disconnected")

async def main():
    async with websockets.serve(handle, "0.0.0.0", WS_PORT):
        print(f"Proxy listening on wss://0.0.0.0:{WS_PORT}/ws")
        await asyncio.Future()

asyncio.run(main())