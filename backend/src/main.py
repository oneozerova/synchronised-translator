import asyncio
import websockets
from websockets.exceptions import ConnectionClosed
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
from src.settings import settings

app = FastAPI()

# stt_url = f"ws://{settings.stt_host}:8001/ws"
stt_url = "wss://apollo2.ci.nsu.ru/i.purtov/proxy/8001/ws"
translator_url = "ws://localhost:8002/ws"


@app.websocket("/ws")
async def websocket_endpoint(client_ws: WebSocket):
    await client_ws.accept()
    print(f"[Proxy] Client connected: {client_ws.client}")

    # Подключаемся к STT и переводчику
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

    try:
        translator_ws = await websockets.connect(
            translator_url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5
        )
        print("[Proxy] Connected to Translator service")
    except Exception as e:
        print(f"[Proxy] Failed to connect to Translator: {e}")
        await server_ws.close()
        await client_ws.close(code=1011)
        return

    shutdown_flag = asyncio.Event()

    async def backend_to_stt():
        """Client → STT"""
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
        """STT → Translator → Client"""
        try:
            while not shutdown_flag.is_set():
                try:
                    msg = await asyncio.wait_for(
                        server_ws.recv(),
                        timeout=30.0
                    )
                    print(f"[STT] Raw: {msg}")

                    # 👇 ВАЖНО: STT шлёт текст (JSON строку)
                    if isinstance(msg, str):
                        try:
                            import json
                            stt_data = json.loads(msg)

                            # 👇 Извлекаем смысловой текст из STT-ответа
                            # Приоритет: stable > pending > fallback
                            text_to_translate = stt_data.get("stable") or stt_data.get("pending") or ""

                            if text_to_translate:
                                # Отправляем в переводчик только чистый текст
                                await translator_ws.send(text_to_translate)

                                # Получаем перевод
                                resp = await asyncio.wait_for(
                                    translator_ws.recv(),
                                    timeout=30.0
                                )

                                # Парсим ответ переводчика
                                if isinstance(resp, str):
                                    trans_data = json.loads(resp)
                                    translated = trans_data.get("translation", text_to_translate)
                                else:
                                    translated = text_to_translate

                                # 👇 Отправляем клиенту уже переведённый текст
                                # (можно обернуть обратно в JSON, если клиент этого ждёт)
                                await client_ws.send_text(json.dumps({
                                    "stable": translated,  # или "translation": translated
                                    "pending": "",
                                    "chars": len(translated),
                                    "speed_logs": stt_data.get("speed_logs", "")
                                }))
                            else:
                                # Пустой текст — пропускаем
                                continue

                        except json.JSONDecodeError as e:
                            print(f"Failed to parse STT JSON: {e}, raw: {msg[:100]}")
                            # Fallback: отправляем как есть
                            await client_ws.send_text(msg)
                        except Exception as e:
                            print(f"Translation pipeline error: {e}")
                            # На ошибке — отправляем оригинал
                            await client_ws.send_text(msg)
                    else:
                        # Бинарные данные — как есть
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
                    print(f"[Proxy] Error in stt_to_backend: {e}")
                    # На ошибке — отправляем оригинал
                    if isinstance(msg, str):
                        await client_ws.send_text(msg)
        finally:
            shutdown_flag.set()

    # Запускаем обе задачи
    task1 = asyncio.create_task(backend_to_stt())
    task2 = asyncio.create_task(stt_to_backend())

    try:
        await asyncio.wait([task1, task2], return_when=asyncio.FIRST_COMPLETED)
    finally:
        # 🔥 Гарантированная очистка
        shutdown_flag.set()

        task1.cancel()
        task2.cancel()
        await asyncio.gather(task1, task2, return_exceptions=True)

        for ws in [server_ws, translator_ws]:
            # Закрываем соединения
            try:
                await ws.close()
            except Exception as e:
                print(f"[Proxy] Error closing server_ws: {e}")

        try:
            if client_ws.client_state == WebSocketState.CONNECTED:
                await client_ws.close()
        except Exception as e:
            print(f"[Proxy] Error closing client_ws: {e}")

        print("[Proxy] Connection closed, resources cleaned")
