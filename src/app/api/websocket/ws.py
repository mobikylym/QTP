from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active: dict[str, list[WebSocket]] = {}

    def get_room_key(self, room_type: str, room_id: str) -> str:
        """Генерирует ключ комнаты"""
        return f'{room_type}_{room_id}'

    async def connect(self, websocket: WebSocket, room_type: str, room_id: str):
        await websocket.accept()
        room_key = self.get_room_key(room_type, room_id)
        if room_key not in self.active:
            self.active[room_key] = []
        self.active[room_key].append(websocket)

    def disconnect(self, websocket: WebSocket, room_type: str, room_id: str):
        room_key = self.get_room_key(room_type, room_id)
        conns = self.active.get(room_key, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns:
            del self.active[room_key]

    async def send_to_room(self, room_type: str, room_id: str, message: dict):
        """Отправляет сообщение всем подключенным к комнате"""
        room_key = self.get_room_key(room_type, room_id)
        conns = self.active.get(room_key, [])

        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()
