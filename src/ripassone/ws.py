"""WebSocket: dispatch eventi tipizzati + broadcast state/full + countdown loop.

Protocollo:
  Client -> Server: { "type": "ns/event", "data": {...} }
  Server -> Client: { "type": "state/full", "data": {GameState} }
                    { "type": "countdown/tick", "data": {"seconds_left": int} }
                    { "type": "state/error", "msg": "..." }     (solo al mittente)
                    { "type": "team/joined", "data": {Player} } (solo al mittente)

L'orchestrator del countdown vive qui (non in state.py) per separare
la logica di gioco (state) dal lifecycle async (ws).
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ripassone import auth, state
from ripassone.models import Phase, Settings

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

# Mappa player_id -> WebSocket per kick di sessioni duplicate e routing diretto.
# Vive qui (non in state.py) perche l'oggetto WebSocket appartiene al
# lifecycle async della connessione, non al modello di gioco.
_player_ws: dict[str, WebSocket] = {}


def _redact_round_for_viewer(round_dict: dict, phase: str, viewer_player_id: str | None) -> None:
    """Mutua un dict-Round per nascondere informazioni che leakerebbero il gioco:
      - durante TURN_QUESTION: nasconde le lettere delle risposte di tutti
        (tranne quella del viewer), e azzera answer_letter/answer_team_id/is_correct
        (la prima risposta scorante sull'open trapelerebbe il pensiero del primo).
      - SEMPRE: filtra member_votes mostrandoli solo al capitano della squadra
        del votante; gli altri (incluso admin/display) ricevono {}.
        Eccezione: il votante stesso vede il proprio voto."""
    viewer_team_id: str | None = None
    viewer_is_captain = False
    if viewer_player_id is not None:
        viewer = state.STATE.players.get(viewer_player_id)
        if viewer is not None:
            viewer_team_id = viewer.team_id
            if viewer_team_id is not None:
                team = state.STATE.teams.get(viewer_team_id)
                if team is not None and team.captain_id == viewer_player_id:
                    viewer_is_captain = True

    # member_votes: per-viewer
    raw_votes = round_dict.get("member_votes") or {}
    if viewer_is_captain and viewer_team_id is not None:
        # capitano: solo voti dei propri membri
        filtered = {
            pid: letter for pid, letter in raw_votes.items()
            if (p := state.STATE.players.get(pid)) is not None and p.team_id == viewer_team_id
        }
    elif viewer_player_id is not None and viewer_player_id in raw_votes:
        # membro non-capitano: solo il proprio voto
        filtered = {viewer_player_id: raw_votes[viewer_player_id]}
    else:
        filtered = {}
    round_dict["member_votes"] = filtered

    # answers + answer_* nascosti durante TURN_QUESTION
    if phase == Phase.TURN_QUESTION.value:
        redacted: list[dict] = []
        for a in round_dict.get("answers") or []:
            r = {"team_id": a["team_id"], "captain_id": a["captain_id"], "order": a["order"]}
            if viewer_player_id is not None and a["captain_id"] == viewer_player_id:
                # il viewer-capitano vede la propria letter (cortesia post-refresh)
                r["letter"] = a["letter"]
                r["is_correct"] = a["is_correct"]
                r["scored"] = a["scored"]
            redacted.append(r)
        round_dict["answers"] = redacted
        round_dict["answer_letter"] = None
        round_dict["answer_team_id"] = None
        round_dict["is_correct"] = None


def state_snapshot_for(viewer_player_id: str | None) -> dict:
    """Snapshot filtrato per il viewer indicato (None = nessun privilegio)."""
    data = state.STATE.model_dump(mode="json")
    rounds = data.get("rounds") or []
    if rounds:
        # filtriamo solo il round corrente: i precedenti sono storia,
        # gia rivelati, niente da nascondere.
        _redact_round_for_viewer(rounds[-1], data.get("phase", ""), viewer_player_id)
    return {"type": "state/full", "data": data}


# Backward-compat: snapshot pubblico (nessun viewer, filtro massimo).
def state_snapshot() -> dict:
    return state_snapshot_for(None)


async def broadcast_state() -> None:
    """Invia state/full a ogni connessione, costruito ad hoc per il viewer
    (player associato a quella ws). Connessioni senza player associato
    ricevono lo snapshot filtrato pubblico (display, admin pre-login client-side)."""
    # ws -> player_id, calcolato al volo
    ws_to_pid: dict[WebSocket, str] = {ws_: pid for pid, ws_ in _player_ws.items()}
    dead: list[WebSocket] = []
    # snapshot pubblico riusato per ws senza player associato (display ecc.)
    public_msg = state_snapshot_for(None)
    for ws in manager.active:
        pid = ws_to_pid.get(ws)
        msg = state_snapshot_for(pid) if pid is not None else public_msg
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        manager.disconnect(ws)


async def kick_old_session(player_id: str, current_ws: WebSocket | None = None) -> None:
    """Manda team/kicked al WebSocket precedente di player_id e lo chiude.
    Se current_ws == old_ws (ricollegamento sulla stessa connessione), no-op."""
    old_ws = _player_ws.get(player_id)
    if old_ws is None or old_ws is current_ws:
        return
    try:
        await old_ws.send_json({
            "type": "team/kicked",
            "msg": "Un altro accesso ha sostituito la tua sessione.",
        })
        await old_ws.close()
    except Exception:
        pass
    manager.disconnect(old_ws)
    _player_ws.pop(player_id, None)


def _cleanup_ws_map(ws: WebSocket) -> str | None:
    """Rimuove ws da _player_ws (se presente) e marca il player offline.
    Ritorna il player_id se trovato, None altrimenti."""
    for pid, w in list(_player_ws.items()):
        if w is ws:
            _player_ws.pop(pid, None)
            player = state.STATE.players.get(pid)
            if player:
                player.online = False
            return pid
    return None


# ============================================================
# Countdown server-side
# ============================================================
_countdown_task: asyncio.Task | None = None


async def _countdown_runner() -> None:
    """Decrementa il countdown ogni secondo finche siamo in TURN_QUESTION.
    A 0 applica scoring di timeout (in state.tick_countdown) e fa broadcast
    state/full. Se la phase cambia (perche un capitano ha risposto), si ferma."""
    try:
        while True:
            await asyncio.sleep(1.0)
            result = await state.tick_countdown()
            if result == "tick":
                await manager.broadcast({
                    "type": "countdown/tick",
                    "data": {"seconds_left": state.STATE.countdown_seconds_left},
                })
            elif result == "timeout":
                await broadcast_state()
                return
            else:  # stopped
                return
    except asyncio.CancelledError:
        # cancellazione normale (un capitano ha risposto prima)
        return


def start_countdown() -> None:
    global _countdown_task
    if _countdown_task and not _countdown_task.done():
        _countdown_task.cancel()
    _countdown_task = asyncio.create_task(_countdown_runner())


def stop_countdown() -> None:
    global _countdown_task
    if _countdown_task and not _countdown_task.done():
        _countdown_task.cancel()


# ============================================================
# Event dispatch
# ============================================================
async def _h_admin_configure(ws: WebSocket, data: dict) -> None:
    settings = Settings(**data)
    await state.admin_configure(settings)


async def _h_admin_open_captain_election(ws: WebSocket, data: dict) -> None:
    await state.admin_open_captain_election()


async def _h_admin_back_to_lobby(ws: WebSocket, data: dict) -> None:
    await state.admin_back_to_lobby()


async def _h_admin_close_election(ws: WebSocket, data: dict) -> None:
    await state.admin_close_election()


async def _h_admin_back_to_election(ws: WebSocket, data: dict) -> None:
    await state.admin_back_to_election()


async def _h_admin_start_quiz(ws: WebSocket, data: dict) -> None:
    await state.admin_start_quiz()


async def _h_admin_next_turn(ws: WebSocket, data: dict) -> None:
    await state.admin_next_turn()


async def _h_admin_end_quiz(ws: WebSocket, data: dict) -> None:
    stop_countdown()
    await state.admin_end_quiz()


async def _h_admin_seed_demo(ws: WebSocket, data: dict) -> None:
    await state.admin_seed_demo_questions()


async def _h_admin_reset(ws: WebSocket, data: dict) -> None:
    stop_countdown()
    await state.admin_reset()


async def _h_team_join(ws: WebSocket, data: dict) -> None:
    player, replaced = await state.team_join(
        first_name=data["first_name"],
        last_name=data["last_name"],
        team_name=data["team_name"],
    )
    if replaced:
        await kick_old_session(player.id, current_ws=ws)
    _player_ws[player.id] = ws
    await ws.send_json({"type": "team/joined", "data": player.model_dump(mode="json")})


async def _h_team_rejoin(ws: WebSocket, data: dict) -> None:
    """Riconnette uno studente con (nome, cognome). Ammesso in qualsiasi fase.
    Il check anti-scherzo (sessione attiva → rifiuto) e in state.team_rejoin."""
    player, replaced = await state.team_rejoin(
        first_name=data["first_name"],
        last_name=data["last_name"],
    )
    if replaced:
        await kick_old_session(player.id, current_ws=ws)
    _player_ws[player.id] = ws
    await ws.send_json({"type": "team/joined", "data": player.model_dump(mode="json")})


async def _h_team_heartbeat(ws: WebSocket, data: dict) -> None:
    """Heartbeat dal client (~ogni 10s). Aggiorna last_seen, niente broadcast."""
    pid = data.get("player_id")
    if not pid:
        return
    await state.team_heartbeat(player_id=pid)


async def _h_team_promote_captain(ws: WebSocket, data: dict) -> None:
    await state.team_promote_captain(player_id=data["player_id"])


async def _h_team_vote(ws: WebSocket, data: dict) -> None:
    await state.team_vote(player_id=data["player_id"], option=data["option"])


async def _h_team_propose_choice(ws: WebSocket, data: dict) -> None:
    await state.team_propose_choice(
        player_id=data["player_id"],
        question_id=data.get("question_id"),
        bet=data.get("bet"),
        target=data.get("target"),
    )


async def _h_team_change_team(ws: WebSocket, data: dict) -> None:
    await state.team_change_team(
        player_id=data["player_id"],
        target_team_name=data["team_name"],
    )


async def _h_team_edit_self(ws: WebSocket, data: dict) -> None:
    await state.team_edit_self(
        player_id=data["player_id"],
        first_name=data["first_name"],
        last_name=data["last_name"],
    )


async def _h_team_leave(ws: WebSocket, data: dict) -> None:
    pid = data["player_id"]
    await state.team_leave(pid)
    if _player_ws.get(pid) is ws:
        _player_ws.pop(pid, None)


async def _h_team_vote_captain(ws: WebSocket, data: dict) -> None:
    await state.team_vote_captain(
        voter_id=data["voter_id"],
        candidate_id=data["candidate_id"],
        grade=int(data["grade"]),
    )


async def _h_team_rename_team(ws: WebSocket, data: dict) -> None:
    await state.team_rename_team(
        player_id=data["player_id"],
        new_name=data["new_name"],
    )


async def _h_captain_choose_question(ws: WebSocket, data: dict) -> None:
    await state.captain_choose_question(
        captain_id=data["captain_id"],
        question_id=int(data["question_id"]),
        bet=int(data["bet"]),
        target=data["target"],
    )
    # parte il countdown server-side
    start_countdown()


async def _h_captain_answer(ws: WebSocket, data: dict) -> None:
    await state.captain_answer(captain_id=data["captain_id"], option=data["option"])
    # Il countdown va fermato solo se l'early reveal e scattato (tutti i
    # capitani eleggibili online hanno risposto). Se siamo ancora in
    # TURN_QUESTION, gli altri capitani devono poter rispondere fino al timer.
    if state.STATE.phase == Phase.TURN_REVEAL.value:
        stop_countdown()


HANDLERS: dict[str, Callable[[WebSocket, dict], Awaitable[None]]] = {
    "admin/configure":             _h_admin_configure,
    "admin/open_captain_election": _h_admin_open_captain_election,
    "admin/back_to_lobby":         _h_admin_back_to_lobby,
    "admin/close_election":        _h_admin_close_election,
    "admin/back_to_election":      _h_admin_back_to_election,
    "admin/start_quiz":            _h_admin_start_quiz,
    "admin/next_turn":             _h_admin_next_turn,
    "admin/end_quiz":              _h_admin_end_quiz,
    "admin/seed_demo":             _h_admin_seed_demo,
    "admin/reset":                 _h_admin_reset,
    "team/join":                   _h_team_join,
    "team/rejoin":                 _h_team_rejoin,
    "team/heartbeat":              _h_team_heartbeat,
    "team/promote_captain":        _h_team_promote_captain,
    "team/vote":                   _h_team_vote,
    "team/propose_choice":         _h_team_propose_choice,
    "team/change_team":            _h_team_change_team,
    "team/edit_self":              _h_team_edit_self,
    "team/leave":                  _h_team_leave,
    "team/vote_captain":           _h_team_vote_captain,
    "team/rename_team":            _h_team_rename_team,
    "captain/choose_question":     _h_captain_choose_question,
    "captain/answer":              _h_captain_answer,
}


# ============================================================
# WS endpoint
# ============================================================
@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    await ws.send_json(state_snapshot())
    is_admin = auth.is_admin_ws(ws)
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
            # gating: solo l'admin (cookie valido) puo inviare eventi admin/*
            if event_type.startswith("admin/") and not is_admin:
                await ws.send_json({
                    "type": "state/error",
                    "msg": f"Non autorizzato per {event_type} (login admin richiesto)",
                })
                continue
            try:
                await handler(ws, data)
                # heartbeat: solo aggiornamento last_seen, niente fan-out
                if event_type != "team/heartbeat":
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
        if _cleanup_ws_map(ws) is not None:
            await broadcast_state()
