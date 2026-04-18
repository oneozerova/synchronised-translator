import asyncio
from websockets.exceptions import ConnectionClosed
from fastapi import WebSocket, WebSocketDisconnect


async def backend_to_stt(client_ws: WebSocket, server_ws):
    """Forward: Client to STT"""
    try:
        while True:
            try:
                data = await asyncio.wait_for(
                    client_ws.receive_bytes(),
                    timeout=30.0
                )
                await server_ws.send(data)
            except WebSocketDisconnect:
                print("[Proxy] Client disconnected (backend_to_stt)")
                break
            except asyncio.TimeoutError:
                continue
            except ConnectionClosed:
                print("[Proxy] STT connection closed (backend_to_stt)")
                break
    except Exception as e:
        print(f"[Proxy] backend_to_stt unexpected error: {type(e).__name__}: {e}")


async def stt_to_backend(client_ws: WebSocket, server_ws):
    """Forward: STT to Client"""
    try:
        while True:
            try:
                data = await asyncio.wait_for(
                    server_ws.recv(),
                    timeout=30.0
                )
                await client_ws.send_bytes(data)
            except WebSocketDisconnect:
                print("[Proxy] Client disconnected (stt_to_backend)")
                break
            except asyncio.TimeoutError:
                continue
            except ConnectionClosed:
                print("[Proxy] STT connection closed (stt_to_backend)")
                break
    except Exception as e:
        print(f"[Proxy] stt_to_backend unexpected error: {type(e).__name__}: {e}")