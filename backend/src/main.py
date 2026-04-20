import asyncio
import websockets
from websockets.exceptions import ConnectionClosed
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
from src.settings import settings

app = FastAPI()

# stt_url = f"ws://{settings.stt_host}:8001/ws"
stt_url = "wss://apollo2.ci.nsu.ru/i.purtov/proxy/8000/ws"
print(stt_url)


@app.websocket("/ws")
async def websocket_endpoint(client_ws: WebSocket):
    await client_ws.accept()
    print(f"[Proxy] Client connected: {client_ws.client}")

    # Подключаемся к STT-серверу
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

    # Флаг для корректного завершения задач
    shutdown_flag = asyncio.Event()

    async def backend_to_stt():
        """Forward: Client → STT"""
        try:
            while not shutdown_flag.is_set():
                try:
                    # Получаем данные от клиента с таймаутом
                    data = await asyncio.wait_for(
                        client_ws.receive_bytes(),
                        timeout=30.0
                    )
                    # Отправляем на STT-сервер
                    await server_ws.send(data)
                except WebSocketDisconnect:
                    print("[Proxy] Client disconnected (backend_to_stt)")
                    break
                except asyncio.TimeoutError:
                    # Проверяем, не пора ли завершаться
                    if shutdown_flag.is_set():
                        break
                    continue
                except ConnectionClosed:
                    print("[Proxy] STT connection closed (backend_to_stt)")
                    break
        except Exception as e:
            print(f"[Proxy] backend_to_stt unexpected error: {type(e).__name__}: {e}")
        finally:
            shutdown_flag.set()

    async def stt_to_backend():
        """Forward: STT → Client (TEXT!)"""
        try:
            while not shutdown_flag.is_set():
                try:
                    msg = await asyncio.wait_for(
                        server_ws.recv(),
                        timeout=30.0
                    )

                    print(msg)

                    # 👇 ВАЖНО: STT шлёт текст (JSON строку)
                    if isinstance(msg, str):
                        await client_ws.send_text(msg)
                    else:
                        # на всякий случай (если вдруг байты)
                        await client_ws.send_bytes(msg)

                except WebSocketDisconnect:
                    print("[Proxy] Client disconnected (stt_to_backend)")
                    break
                except asyncio.TimeoutError:
                    if shutdown_flag.is_set():
                        break
                    continue
                except ConnectionClosed:
                    print("[Proxy] STT connection closed (stt_to_backend)")
                    break

        except Exception as e:
            print(f"[Proxy] stt_to_backend error: {e}")
        finally:
            shutdown_flag.set()

    # Запускаем обе задачи
    task1 = asyncio.create_task(backend_to_stt())
    task2 = asyncio.create_task(stt_to_backend())

    try:
        # Ждём завершения любой из задач
        await asyncio.wait(
            [task1, task2],
            return_when=asyncio.FIRST_COMPLETED
        )
    except Exception as e:
        print(f"[Proxy] Unexpected error in main loop: {type(e).__name__}: {e}")
    finally:
        # 🔥 Гарантированная очистка
        shutdown_flag.set()

        task1.cancel()
        task2.cancel()
        await asyncio.gather(task1, task2, return_exceptions=True)

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