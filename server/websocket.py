"""WebSocket handler with connection lifecycle management.

Authentication precedence (most-secure first):
1. ``Authorization: Bearer <token>`` header on the upgrade request.
2. ``Sec-WebSocket-Protocol: bearer, <token>`` subprotocol — passed by
   browsers that cannot set arbitrary headers on the WS handshake. The
   server must echo the ``bearer`` subprotocol on accept or the browser
   will refuse the connection.
3. ``?token=<token>`` query string — fallback only, logged at WARNING
   because tokens leak into access logs and browser history.

Any auth failure closes the socket with policy-violation code 4401.
Broadcast errors remove the offending socket from the active set instead
of silently swallowing the exception, preventing dead-connection leaks.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.auth import decode_token

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    """Manage active WebSocket connections."""

    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(
        self, websocket: WebSocket, user_id: str, subprotocol: str | None = None
    ) -> None:
        """Accept the handshake, optionally echoing a subprotocol."""
        if subprotocol:
            await websocket.accept(subprotocol=subprotocol)
        else:
            await websocket.accept()
        self.active_connections[user_id] = websocket

    def disconnect(self, user_id: str) -> None:
        self.active_connections.pop(user_id, None)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send ``message`` to all active sockets; drop any that fail."""
        stale: list[str] = []
        for user_id, ws in list(self.active_connections.items()):
            try:
                await ws.send_json(message)
            except Exception as exc:  # noqa: BLE001  intentional catch-all
                logger.warning(
                    "Broadcast to %s failed (%s); dropping connection",
                    user_id,
                    exc,
                )
                stale.append(user_id)
        for user_id in stale:
            self.active_connections.pop(user_id, None)


manager = ConnectionManager()


def _extract_token(websocket: WebSocket, query_token: str) -> tuple[str, str | None]:
    """Return ``(token, subprotocol_to_echo)``.

    ``subprotocol_to_echo`` is non-None only when the client negotiated
    the ``bearer`` subprotocol and we must mirror it on accept.
    """
    # 1. Authorization header
    auth = websocket.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[len("bearer ") :].strip(), None

    # 2. Sec-WebSocket-Protocol: "bearer, <token>"
    raw = websocket.headers.get("sec-websocket-protocol", "")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) >= 2 and parts[0].lower() == "bearer":
        return parts[1], "bearer"

    # 3. Query string fallback (discouraged)
    if query_token:
        logger.warning(
            "WebSocket auth via ?token= query is discouraged (leaks into logs). "
            "Prefer an Authorization header or the 'bearer' subprotocol."
        )
        return query_token, None

    return "", None


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = "") -> None:
    """WebSocket endpoint with JWT auth."""
    resolved_token, subprotocol = _extract_token(websocket, token)
    if not resolved_token:
        await websocket.close(code=4401)
        return

    try:
        payload = decode_token(resolved_token)
    except Exception:
        await websocket.close(code=4401)
        return

    if payload.get("type") != "access":
        await websocket.close(code=4401)
        return

    user_id = str(payload.get("sub", "anonymous"))
    await manager.connect(websocket, user_id, subprotocol=subprotocol)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(user_id)
    except Exception as exc:  # noqa: BLE001  last-resort cleanup
        logger.warning("WebSocket error for %s: %s", user_id, exc)
        manager.disconnect(user_id)
