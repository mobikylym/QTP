from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src.app.api.http import entities as entities_api
from src.app.api.websocket.ws import manager
from src.app.core.config import settings
from src.app.storage.database import init_models

app = FastAPI(title=settings.APP_NAME)
app.include_router(entities_api.router, prefix='/api')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:8501'],  # streamlit dev UI
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.on_event('startup')
async def startup_event():
    # создаём таблицы (для прототипа)
    await init_models()


@app.websocket('/ws/{channel_id}')
async def ws_channel(ws: WebSocket, channel_id: str):
    await manager.connect(ws, channel_id)
    try:
        while True:
            data = await ws.receive_text()  # keepalive / client messages ignored for prototype
            # echo or ignore
    except WebSocketDisconnect:
        manager.disconnect(ws, channel_id)
