"""GameState singleton + state machine + handlers di evento.

Il modulo espone:
- STATE: l'istanza unica del GameState in RAM
- transition(): cambio di phase con check delle transizioni valide
- handler async per ogni evento WebSocket (admin/team/captain)

Tutti gli handler che mutano lo stato passano per _lock per evitare
race conditions con eventi concorrenti.
"""
from __future__ import annotations

import asyncio
import random
import uuid

from ripassone.models import (
    GameState,
    Letter,
    Phase,
    Player,
    Round,
    Settings,
    Team,
)

# Singleton in-process
STATE = GameState()
_lock = asyncio.Lock()


# ============================================================
# State machine: transizioni ammesse
# ============================================================
VALID_TRANSITIONS: dict[Phase, set[Phase]] = {
    Phase.SETUP:          {Phase.LOBBY},
    Phase.LOBBY:          {Phase.SETUP, Phase.READY_TO_START},
    Phase.READY_TO_START: {Phase.LOBBY, Phase.TURN_CHOICE},
    Phase.TURN_CHOICE:    {Phase.TURN_QUESTION, Phase.FINISHED},
    Phase.TURN_QUESTION:  {Phase.TURN_REVEAL},
    Phase.TURN_REVEAL:    {Phase.TURN_CHOICE, Phase.FINISHED},
    Phase.FINISHED:       {Phase.SETUP},
}

# Palette colori per le squadre (cartoon-pop)
TEAM_COLORS = [
    "#D9A52F",  # giallo deep
    "#2A8893",  # teal deep
    "#E84C3D",  # coral
    "#9B6BD3",  # purple
    "#15803D",  # green deep
    "#7C2D12",  # siena
    "#0E7490",  # cyan deep
    "#DB2777",  # pink
]


class StateError(Exception):
    """Errore semantico dello stato (transizione non valida, vincolo violato, ...).

    Verra inviato al solo client mittente come state/error, non broadcast.
    """


def transition(new_phase: Phase) -> None:
    current = Phase(STATE.phase) if isinstance(STATE.phase, str) else STATE.phase
    allowed = VALID_TRANSITIONS.get(current, set())
    if new_phase not in allowed:
        raise StateError(
            f"Transizione non valida: {current.value} -> {new_phase.value}. "
            f"Da {current.value} si puo solo: {[p.value for p in allowed]}"
        )
    STATE.phase = new_phase


# ============================================================
# Helpers
# ============================================================
def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _next_team_color() -> str:
    used = {t.color for t in STATE.teams.values()}
    for c in TEAM_COLORS:
        if c not in used:
            return c
    return TEAM_COLORS[len(STATE.teams) % len(TEAM_COLORS)]


def _team_by_name(name: str) -> Team | None:
    norm = name.strip().upper()
    for t in STATE.teams.values():
        if t.name.upper() == norm:
            return t
    return None


# ============================================================
# Handlers — eventi admin
# ============================================================
async def admin_configure(settings: Settings) -> None:
    """Imposta i parametri del quiz e passa in LOBBY."""
    async with _lock:
        if STATE.phase not in (Phase.SETUP, Phase.LOBBY):
            raise StateError(
                "Le impostazioni si possono modificare solo in SETUP o LOBBY"
            )
        STATE.settings = settings
        # propaga i punti iniziali alle squadre gia esistenti che sono ancora a 0
        for t in STATE.teams.values():
            if t.score == 0:
                t.score = settings.initial_points
        if STATE.phase == Phase.SETUP:
            transition(Phase.LOBBY)


async def admin_start_quiz() -> None:
    """LOBBY -> READY -> TURN_CHOICE: sorteggia ordine, apre primo turno."""
    async with _lock:
        if STATE.phase != Phase.LOBBY:
            raise StateError("Lo start si puo fare solo dalla LOBBY")
        if len(STATE.teams) < 2:
            raise StateError("Servono almeno 2 squadre per iniziare")
        for t in STATE.teams.values():
            if t.captain_id is None:
                raise StateError(f"La squadra {t.name} non ha un capitano")

        # sorteggia l'ordine dei team
        order = list(STATE.teams.keys())
        random.shuffle(order)
        STATE.turn_order = order
        STATE.current_turn_idx = 0

        transition(Phase.READY_TO_START)
        _open_new_round()
        transition(Phase.TURN_CHOICE)


async def admin_next_turn() -> None:
    """TURN_REVEAL -> TURN_CHOICE (incrementa turno) o -> FINISHED se ultimo round."""
    async with _lock:
        if STATE.phase != Phase.TURN_REVEAL:
            raise StateError("next_turn solo da TURN_REVEAL")

        # quanti round abbiamo gia chiuso
        if len(STATE.rounds) >= STATE.settings.rounds:
            transition(Phase.FINISHED)
            return

        STATE.current_turn_idx = (STATE.current_turn_idx + 1) % len(STATE.turn_order)
        _open_new_round()
        transition(Phase.TURN_CHOICE)


def _open_new_round() -> None:
    """Crea un nuovo Round con la squadra di turno corrente."""
    asking = STATE.asking_team_id
    if asking is None:
        raise StateError("Nessuna squadra di turno definita")
    STATE.rounds.append(Round(
        number=len(STATE.rounds) + 1,
        asking_team_id=asking,
    ))


# ============================================================
# Handlers — eventi team / studente
# ============================================================
async def team_join(first_name: str, last_name: str, team_name: str) -> Player:
    """Aggiunge un giocatore. Se la squadra esiste, lo iscrive come membro.
    Se la squadra e nuova, viene creata e il giocatore e capitano automatico.

    Ritorna il Player creato (con id da memorizzare client-side).
    """
    async with _lock:
        if STATE.phase not in (Phase.SETUP, Phase.LOBBY):
            raise StateError("I giocatori possono entrare solo in SETUP/LOBBY")

        # se siamo ancora in SETUP, l'arrivo del primo giocatore apre la LOBBY
        if STATE.phase == Phase.SETUP:
            transition(Phase.LOBBY)

        first_name = first_name.strip()
        last_name = last_name.strip()
        team_name = team_name.strip()

        if not first_name or not last_name or not team_name:
            raise StateError("Nome, cognome e squadra sono tutti obbligatori")

        # squadra: esistente o nuova
        team = _team_by_name(team_name)
        if team is None:
            team = Team(
                id=_new_id(),
                name=team_name.upper(),
                color=_next_team_color(),
                score=STATE.settings.initial_points,
            )
            STATE.teams[team.id] = team

        if len(STATE.teams) > 8:
            raise StateError("Massimo 8 squadre")

        player = Player(
            id=_new_id(),
            first_name=first_name,
            last_name=last_name,
            team_id=team.id,
        )
        STATE.players[player.id] = player

        # primo giocatore della squadra: diventa capitano automatico
        if team.captain_id is None:
            team.captain_id = player.id

        return player


async def team_promote_captain(player_id: str) -> None:
    """Cede la fascia di capitano a un altro membro della stessa squadra."""
    async with _lock:
        if STATE.phase not in (Phase.LOBBY,):
            raise StateError("La fascia di capitano si cambia solo in LOBBY")
        player = STATE.players.get(player_id)
        if player is None or player.team_id is None:
            raise StateError("Giocatore o squadra non trovati")
        team = STATE.teams[player.team_id]
        team.captain_id = player.id


# ============================================================
# Handlers — eventi capitano (durante il turno)
# ============================================================
async def captain_choose_question(captain_id: str, question_id: int, bet: int, target: str) -> None:
    """TURN_CHOICE -> TURN_QUESTION: il capitano della squadra di turno
    sceglie domanda + puntata + target ('open' o team_id avversario)."""
    async with _lock:
        if STATE.phase != Phase.TURN_CHOICE:
            raise StateError("Scelta domanda solo in TURN_CHOICE")

        round_ = STATE.current_round
        if round_ is None:
            raise StateError("Nessun round aperto")

        asking = STATE.teams[round_.asking_team_id]
        if asking.captain_id != captain_id:
            raise StateError("Solo il capitano della squadra di turno puo scegliere")

        if question_id in STATE.used_question_ids:
            raise StateError("Domanda gia usata in questo quiz")
        if question_id not in STATE.questions_pool:
            raise StateError(f"Domanda {question_id} non presente nel pool")

        s = STATE.settings
        if not (s.min_bet <= bet <= s.max_bet):
            raise StateError(f"Puntata fuori range [{s.min_bet}, {s.max_bet}]")
        if bet > asking.score:
            raise StateError(f"Puntata superiore ai punti disponibili ({asking.score})")

        if target != "open" and target not in STATE.teams:
            raise StateError("Target non valido")
        if target == round_.asking_team_id:
            raise StateError("Non puoi rivolgere la domanda alla tua stessa squadra")

        round_.question_id = question_id
        round_.bet = bet
        round_.target = target
        STATE.used_question_ids.add(question_id)
        STATE.countdown_seconds_left = s.seconds

        transition(Phase.TURN_QUESTION)


async def captain_answer(captain_id: str, option: Letter) -> None:
    """TURN_QUESTION -> TURN_REVEAL: il primo capitano che risponde blocca.
    NB: la logica completa di scoring (corretto/sbagliato, distribuzione
    punti per 'aperta a tutti' o squadra specifica) arrivera in tappa 3.
    Qui per ora si registra solo la risposta e si passa a REVEAL.
    """
    async with _lock:
        if STATE.phase != Phase.TURN_QUESTION:
            raise StateError("Risposta solo in TURN_QUESTION")
        round_ = STATE.current_round
        if round_ is None or round_.answer_letter is not None:
            raise StateError("Round non valido o gia risposto")

        # trova la squadra del capitano che risponde
        answering_team_id = None
        for t in STATE.teams.values():
            if t.captain_id == captain_id:
                answering_team_id = t.id
                break
        if answering_team_id is None:
            raise StateError("Solo i capitani possono rispondere")

        # validazione target: se target e una squadra specifica, solo quella puo rispondere
        if round_.target != "open" and round_.target != answering_team_id:
            raise StateError("Questa domanda e indirizzata a un'altra squadra")

        round_.answer_letter = option
        round_.answer_team_id = answering_team_id
        STATE.countdown_seconds_left = None

        # scoring placeholder (Tappa 3 lo riempira correttamente)
        question = STATE.questions_pool[round_.question_id]
        is_correct = option == question.correct
        bet = round_.bet or 0
        if is_correct:
            STATE.teams[answering_team_id].score += bet
            STATE.teams[round_.asking_team_id].score -= bet
            round_.points_delta = {answering_team_id: +bet, round_.asking_team_id: -bet}
        else:
            STATE.teams[answering_team_id].score -= bet
            STATE.teams[round_.asking_team_id].score += bet
            round_.points_delta = {answering_team_id: -bet, round_.asking_team_id: +bet}

        transition(Phase.TURN_REVEAL)
