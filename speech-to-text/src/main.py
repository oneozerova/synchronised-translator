from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("[STT] Client connected")

    try:
        while True:
            data = await websocket.receive_bytes()
            print(f"[STT] Received audio chunk: {len(data)} bytes")

            # Эхо: в реальном проекте здесь будет STT-обработка
            await websocket.send_bytes(data)

    except WebSocketDisconnect:
        print("[STT] Client disconnected")
    except Exception as e:
        print(f"[STT] Error: {e}")
    finally:
        # Гарантированное закрытие, если ещё открыто
        if websocket.client_state.name == "CONNECTED":
            await websocket.close()
        print("[STT] WebSocket closed")