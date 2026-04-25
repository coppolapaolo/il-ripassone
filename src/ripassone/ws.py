"""WebSocket: ConnectionManager + endpoint /ws.

Per Tappa 1 il manager fa solo connect/disconnect/broadcast — i ruoli
(admin/team/display) e gli stati di gioco arriveranno nelle prossime
tappe insieme alla state machine.
"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict) -> None:
        # broadcast tollerante: rimuove i client morti senza sollevare
        dead: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


@router.websocket("/ws")
async def ws_echo(ws: WebSocket) -> None:
    """Endpoint di prova: ogni messaggio ricevuto viene rebroadcast a tutti.

    Usato dai 3 mockup (admin/team/display) per dimostrare che il pub/sub
    funziona end-to-end, prima di costruire la vera state machine.
    """
    await manager.connect(ws)
    n_now = len(manager.active)
    await manager.broadcast({"type": "presence", "connected": n_now})
    try:
        while True:
            data = await ws.receive_json()
            await manager.broadcast({"type": "echo", "from": data.get("from", "?"), "payload": data})
    except WebSocketDisconnect:
        manager.disconnect(ws)
        await manager.broadcast({"type": "presence", "connected": len(manager.active)})
