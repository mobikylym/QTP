from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        # channel_id -> list[WebSocket]
        self.active: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, channel_id: str):
        await websocket.accept()
        self.active.setdefault(channel_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket, channel_id: str):
        conns = self.active.get(channel_id, [])
        if websocket in conns:
            conns.remove(websocket)

    async def broadcast(self, channel_id: str, message: dict):
        conns = list(self.active.get(channel_id, []))
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                # ignore broken sockets
                pass


manager = ConnectionManager()
