"""WebSocket handler with Redis pub/sub."""

import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from server.auth import decode_token

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    """Manage active WebSocket connections."""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        self.active_connections[user_id] = websocket

    def disconnect(self, user_id: str):
        self.active_connections.pop(user_id, None)

    async def broadcast(self, message: dict):
        for ws in self.active_connections.values():
            try:
                await ws.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    """WebSocket endpoint with JWT auth."""
    # Validate token
    try:
        payload = decode_token(token)
        user_id = str(payload.get("sub", "anonymous"))
    except Exception:
        await websocket.close(code=4401)
        return

    await manager.connect(websocket, user_id)
    try:
        while True:
            data = await websocket.receive_json()
            # Handle incoming messages (ping, subscribe, etc.)
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(user_id)
