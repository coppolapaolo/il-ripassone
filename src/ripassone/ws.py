"""WebSocket: dispatch eventi tipizzati + broadcast state/full.

Protocollo:
  Client -> Server: { "type": "ns/event", "data": {...} }
  Server -> Client: { "type": "state/full", "data": {GameState} }
                    { "type": "state/error", "msg": "..." }     (solo al mittente)

Tutti gli handler in state.py sono async e protetti da lock; dopo
ogni mutazione lo state/full viene fatto broadcast.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ripassone import state
from ripassone.models import Settings

router = APIRouter()


# ============================================================
# Connection manager
# ============================================================
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
        dead: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


def state_snapshot() -> dict:
    return {"type": "state/full", "data": state.STATE.model_dump(mode="json")}


async def broadcast_state() -> None:
    await manager.broadcast(state_snapshot())


# ============================================================
# Event dispatch
# ============================================================
async def _h_admin_configure(ws: WebSocket, data: dict) -> None:
    settings = Settings(**data)
    await state.admin_configure(settings)


async def _h_admin_start_quiz(ws: WebSocket, data: dict) -> None:
    await state.admin_start_quiz()


async def _h_admin_next_turn(ws: WebSocket, data: dict) -> None:
    await state.admin_next_turn()


async def _h_team_join(ws: WebSocket, data: dict) -> None:
    player = await state.team_join(
        first_name=data["first_name"],
        last_name=data["last_name"],
        team_name=data["team_name"],
    )
    # rispondi al mittente con il proprio player_id (per memorizzazione client-side)
    await ws.send_json({"type": "team/joined", "data": player.model_dump(mode="json")})


async def _h_team_promote_captain(ws: WebSocket, data: dict) -> None:
    await state.team_promote_captain(player_id=data["player_id"])


async def _h_captain_choose_question(ws: WebSocket, data: dict) -> None:
    await state.captain_choose_question(
        captain_id=data["captain_id"],
        question_id=int(data["question_id"]),
        bet=int(data["bet"]),
        target=data["target"],
    )


async def _h_captain_answer(ws: WebSocket, data: dict) -> None:
    await state.captain_answer(
        captain_id=data["captain_id"],
        option=data["option"],
    )


HANDLERS: dict[str, Callable[[WebSocket, dict], Awaitable[None]]] = {
    "admin/configure":         _h_admin_configure,
    "admin/start_quiz":        _h_admin_start_quiz,
    "admin/next_turn":         _h_admin_next_turn,
    "team/join":               _h_team_join,
    "team/promote_captain":    _h_team_promote_captain,
    "captain/choose_question": _h_captain_choose_question,
    "captain/answer":          _h_captain_answer,
}


# ============================================================
# WS endpoint
# ============================================================
@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    # snapshot iniziale al solo nuovo client
    await ws.send_json(state_snapshot())
    try:
        while True:
            msg = await ws.receive_json()
            event_type = msg.get("type", "")
            data = msg.get("data", {}) or {}
            handler = HANDLERS.get(event_type)
            if handler is None:
                await ws.send_json({
                    "type": "state/error",
                    "msg": f"Evento sconosciuto: {event_type}",
                })
                continue
            try:
                await handler(ws, data)
                await broadcast_state()
            except state.StateError as e:
                await ws.send_json({"type": "state/error", "msg": str(e)})
            except (KeyError, ValueError, TypeError) as e:
                await ws.send_json({
                    "type": "state/error",
                    "msg": f"Payload malformato per {event_type}: {e}",
                })
    except WebSocketDisconnect:
        manager.disconnect(ws)
