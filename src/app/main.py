from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from src.app.api.http import auth, home
from src.app.api.http import entities as entities_api
from src.app.api.websocket.ws import manager
from src.app.core.config import settings
from src.app.services.admin_init import ensure_admin_exists
from src.app.storage.database import AsyncSessionLocal, init_models

app = FastAPI(title=settings.APP_NAME)
app.include_router(entities_api.router, prefix='/api')
app.include_router(auth.router)
app.include_router(home.router)


@app.on_event('startup')
async def startup_event():
    # создаём таблицы (для прототипа)
    await init_models()

    async with AsyncSessionLocal() as session:
        await ensure_admin_exists(session)


@app.websocket('/ws/{channel_id}')
async def ws_channel(ws: WebSocket, channel_id: str):
    await manager.connect(ws, channel_id)
    try:
        while True:
            data = await ws.receive_text()  # keepalive / client messages ignored for prototype
            # echo or ignore
    except WebSocketDisconnect:
        manager.disconnect(ws, channel_id)
