from fastapi import FastAPI
from starlette.websockets import WebSocket, WebSocketDisconnect

from src.yandexgpt import TranslationSession

app = FastAPI()


@app.websocket("/ws")
async def websocket_translate(websocket: WebSocket):
    await websocket.accept()
    session = TranslationSession()

    try:
        while True:
            text = await websocket.receive_text()

            translation = session.translate_chunk(text)

            await websocket.send_json({"translation": translation})

    except WebSocketDisconnect:
        # Клиент отключился — сессия удаляется автоматически
        pass
    except Exception as e:
        await websocket.send_json({"error": str(e)})
