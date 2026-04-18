import asyncio
import websockets
from fastapi import FastAPI, WebSocket
from fastapi.websockets import WebSocketState

from src.settings import settings
from src.stt_handlers import backend_to_stt, stt_to_backend

app = FastAPI()

stt_url = f"ws://{settings.stt_host}:8000/ws"


@app.websocket("/ws")
async def websocket_endpoint(client_ws: WebSocket):
    await client_ws.accept()
    print(f"[Proxy] Client connected: {client_ws.client}")

    try:
        server_ws = await websockets.connect(
            stt_url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5
        )
        print("[Proxy] Connected to STT server")
    except Exception as e:
        print(f"[Proxy] Failed to connect to STT: {e}")
        await client_ws.close(code=1011)
        return

    task1 = asyncio.create_task(backend_to_stt(client_ws, server_ws))
    task2 = asyncio.create_task(stt_to_backend(client_ws, server_ws))

    try:
        done, pending = await asyncio.wait(
            [task1, task2],
            return_when=asyncio.FIRST_COMPLETED
        )

        # отменяем вторую задачу
        for task in pending:
            task.cancel()

        await asyncio.gather(*pending, return_exceptions=True)

    except Exception as e:
        print(f"[Proxy] Unexpected error in main loop: {type(e).__name__}: {e}")

    finally:
        # Закрываем соединения
        try:
            await server_ws.close()
        except Exception as e:
            print(f"[Proxy] Error closing server_ws: {e}")

        try:
            if client_ws.client_state == WebSocketState.CONNECTED:
                await client_ws.close()
        except Exception as e:
            print(f"[Proxy] Error closing client_ws: {e}")

        print("[Proxy] Connection closed, resources cleaned")
