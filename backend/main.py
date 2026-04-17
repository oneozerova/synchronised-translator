from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("Client connected")

    try:
        while True:
            data = await websocket.receive_bytes()

            print(f"Received audio chunk: {len(data)} bytes")

            # echo назад
            await websocket.send_bytes(data)

    except WebSocketDisconnect:
        print("Client disconnected")
