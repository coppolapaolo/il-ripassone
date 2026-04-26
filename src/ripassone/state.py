"""GameState singleton + state machine + handlers di evento.

Il modulo espone:
- STATE: l'istanza unica del GameState in RAM
- transition(): cambio di phase con check delle transizioni valide
- handler async per ogni evento WebSocket (admin/team/captain)
- tick_countdown(): chiamata dall'orchestrator (ws.py) ogni secondo

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
    Question,
    QuestionOption,
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
    Phase.SETUP:             {Phase.LOBBY},
    Phase.LOBBY:             {Phase.SETUP, Phase.CAPTAIN_ELECTION},
    Phase.CAPTAIN_ELECTION:  {Phase.LOBBY, Phase.PRE_GAME},
    Phase.PRE_GAME:          {Phase.CAPTAIN_ELECTION, Phase.READY_TO_START},
    Phase.READY_TO_START:    {Phase.LOBBY, Phase.TURN_CHOICE},
    Phase.TURN_CHOICE:       {Phase.TURN_QUESTION, Phase.FINISHED},
    Phase.TURN_QUESTION:     {Phase.TURN_REVEAL},
    Phase.TURN_REVEAL:       {Phase.TURN_CHOICE, Phase.FINISHED},
    Phase.FINISHED:          {Phase.SETUP},
}

# Scala Majority Judgment (5 livelli, 5=migliore)
MJ_GRADE_MIN = 1
MJ_GRADE_MAX = 5
MJ_LABELS = {
    5: "Eccellente",
    4: "Buono",
    3: "Accettabile",
    2: "Scarso",
    1: "Inadeguato",
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


def _phase() -> Phase:
    """Helper: ritorna la phase corrente come enum (anche se serializzata come stringa)."""
    p = STATE.phase
    return Phase(p) if isinstance(p, str) else p


def transition(new_phase: Phase) -> None:
    current = _phase()
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


def _team_of_captain(captain_id: str) -> Team | None:
    for t in STATE.teams.values():
        if t.captain_id == captain_id:
            return t
    return None


def _find_player_in_team(first: str, last: str, team_id: str) -> Player | None:
    """Cerca un giocatore con (nome, cognome) case-insensitive in una squadra."""
    f, l = first.strip().lower(), last.strip().lower()
    for p in STATE.players.values():
        if (p.team_id == team_id
                and p.first_name.lower() == f
                and p.last_name.lower() == l):
            return p
    return None


def _cleanup_team_after_member_loss(team_id: str, lost_player_id: str) -> None:
    """Riassegna capitano (se necessario) e cancella la squadra se rimasta vuota."""
    team = STATE.teams.get(team_id)
    if team is None:
        return
    if team.captain_id == lost_player_id:
        others = [p for p in STATE.players.values() if p.team_id == team_id]
        team.captain_id = others[0].id if others else None
    if not any(p.team_id == team_id for p in STATE.players.values()):
        STATE.teams.pop(team_id, None)
        STATE.captain_votes.pop(team_id, None)
        STATE.provisional_captains.pop(team_id, None)
    else:
        # rimuovi voti dati o ricevuti dal giocatore uscito
        ballots = STATE.captain_votes.get(team_id, {})
        ballots.pop(lost_player_id, None)
        for vid in list(ballots.keys()):
            ballots[vid].pop(lost_player_id, None)


# ============================================================
# Majority Judgment (Balinski-Laraki 2010)
# ============================================================
def _lower_median_sequence(grades: list[int]) -> tuple[int, ...]:
    """Sequenza dei valori centrali (lower median) estratti uno alla volta.

    Confronto lessicografico: il maggiore vince. Equivalente al "majority gauge"
    classico ma deterministico per pareggi (no random).

    Esempio: [3,4,5,5] -> sorted [3,4,5,5] -> pop idx 1 (=4), [3,5,5] -> pop idx 1 (=5)
             -> [3,5] -> pop idx 0 (=3) -> [5] -> pop 5  =>  (4,5,3,5)
    """
    g = sorted(grades)
    seq: list[int] = []
    while g:
        n = len(g)
        idx = (n - 1) // 2
        seq.append(g.pop(idx))
    return tuple(seq)


def compute_captain_mj(team_id: str) -> str | None:
    """Calcola il capitano di una squadra usando Majority Judgment.

    - Se la squadra ha 1 solo membro: e capitano automatico.
    - Se nessuno ha ancora votato: ritorna None.
    - Altrimenti: per ogni candidato che ha ricevuto voti calcola la
      lower-median sequence; vince la sequenza maggiore (lex).
    """
    members = [p for p in STATE.players.values() if p.team_id == team_id]
    if not members:
        return None
    if len(members) == 1:
        return members[0].id

    ballots = STATE.captain_votes.get(team_id, {})
    received: dict[str, list[int]] = {}
    for grades in ballots.values():
        for cand_id, g in grades.items():
            received.setdefault(cand_id, []).append(int(g))

    candidates = [m for m in members if received.get(m.id)]
    if not candidates:
        return None

    candidates.sort(key=lambda c: _lower_median_sequence(received[c.id]), reverse=True)
    return candidates[0].id


def _recompute_provisional_captain(team_id: str) -> None:
    """Aggiorna provisional_captains[team_id] dopo un voto/cambiamento."""
    cap = compute_captain_mj(team_id)
    if cap is None:
        STATE.provisional_captains.pop(team_id, None)
    else:
        STATE.provisional_captains[team_id] = cap


# ============================================================
# Handlers — eventi admin
# ============================================================
async def admin_configure(settings: Settings) -> None:
    """Imposta i parametri della sfida e passa in LOBBY."""
    async with _lock:
        if _phase() not in (Phase.SETUP, Phase.LOBBY):
            raise StateError(
                "Le impostazioni si possono modificare solo in SETUP o LOBBY"
            )
        STATE.settings = settings
        # propaga i punti iniziali alle squadre gia esistenti che sono ancora a 0
        for t in STATE.teams.values():
            if t.score == 0:
                t.score = settings.initial_points
        if _phase() == Phase.SETUP:
            transition(Phase.LOBBY)


async def admin_open_captain_election() -> None:
    """LOBBY -> CAPTAIN_ELECTION. Validazione: ogni squadra >= 2 membri.

    Inizializza la struttura captain_votes vuota per ogni squadra; il
    capitano provvisorio resta None finche non arriva almeno un voto.
    """
    async with _lock:
        if _phase() != Phase.LOBBY:
            raise StateError("Le elezioni si aprono solo dalla LOBBY")
        if len(STATE.teams) < 2:
            raise StateError("Servono almeno 2 squadre per iniziare le elezioni")
        for t in STATE.teams.values():
            members = [p for p in STATE.players.values() if p.team_id == t.id]
            if len(members) < 2:
                raise StateError(
                    f"La squadra {t.name} ha meno di 2 membri: "
                    "fai cambiare squadra al giocatore singolo prima di avviare le elezioni"
                )
        STATE.captain_votes = {t.id: {} for t in STATE.teams.values()}
        STATE.provisional_captains = {}
        transition(Phase.CAPTAIN_ELECTION)


async def admin_back_to_lobby() -> None:
    """CAPTAIN_ELECTION -> LOBBY: riapre l'iscrizione (es. per aggiungere
    membri a una squadra che e diventata da 1)."""
    async with _lock:
        if _phase() != Phase.CAPTAIN_ELECTION:
            raise StateError("Si torna in LOBBY solo dalla fase elezioni")
        STATE.captain_votes = {}
        STATE.provisional_captains = {}
        transition(Phase.LOBBY)


async def admin_close_election() -> None:
    """CAPTAIN_ELECTION -> PRE_GAME: finalizza i capitani via Majority Judgment.

    Per ogni squadra: se ci sono voti, vince il MJ; altrimenti rimane il
    capitano corrente (primo entrato). Pulisce i dati di voto (non servono piu).
    """
    async with _lock:
        if _phase() != Phase.CAPTAIN_ELECTION:
            raise StateError("Si chiudono le elezioni solo dalla fase elezioni")
        for t in STATE.teams.values():
            mj_winner = compute_captain_mj(t.id)
            if mj_winner is not None:
                t.captain_id = mj_winner
            elif t.captain_id is None:
                members = [p for p in STATE.players.values() if p.team_id == t.id]
                if not members:
                    raise StateError(f"La squadra {t.name} e vuota")
                t.captain_id = members[0].id
        STATE.captain_votes = {}
        STATE.provisional_captains = {}
        transition(Phase.PRE_GAME)


async def admin_back_to_election() -> None:
    """PRE_GAME -> CAPTAIN_ELECTION: riapre le elezioni (es. squadra che
    perde il capitano o vuole ri-eleggere). Inizializza nuove urne vuote."""
    async with _lock:
        if _phase() != Phase.PRE_GAME:
            raise StateError("Si torna alle elezioni solo dal pre-game")
        STATE.captain_votes = {t.id: {} for t in STATE.teams.values()}
        STATE.provisional_captains = {}
        transition(Phase.CAPTAIN_ELECTION)


async def admin_start_quiz() -> None:
    """PRE_GAME -> READY -> TURN_CHOICE: sorteggia ordine, apre primo turno.

    I capitani sono gia stati finalizzati in admin_close_election.
    """
    async with _lock:
        if _phase() != Phase.PRE_GAME:
            raise StateError(
                "Per avviare la sfida bisogna passare per le elezioni e il pre-game"
            )
        if len(STATE.teams) < 2:
            raise StateError("Servono almeno 2 squadre per iniziare")
        if not STATE.questions_pool:
            raise StateError("Il pool domande e vuoto: carica almeno una domanda")
        for t in STATE.teams.values():
            if t.captain_id is None:
                raise StateError(f"La squadra {t.name} non ha un capitano")

        order = list(STATE.teams.keys())
        random.shuffle(order)
        STATE.turn_order = order
        STATE.current_turn_idx = 0

        transition(Phase.READY_TO_START)
        _open_new_round()
        transition(Phase.TURN_CHOICE)


async def admin_next_turn() -> None:
    """TURN_REVEAL -> TURN_CHOICE (incrementa turno) o -> FINISHED se esauriti round."""
    async with _lock:
        if _phase() != Phase.TURN_REVEAL:
            raise StateError("next_turn solo da TURN_REVEAL")

        if len(STATE.rounds) >= STATE.settings.rounds:
            transition(Phase.FINISHED)
            return

        STATE.current_turn_idx = (STATE.current_turn_idx + 1) % len(STATE.turn_order)
        _open_new_round()
        transition(Phase.TURN_CHOICE)


async def admin_end_quiz() -> None:
    """Termine forzato della sfida: ovunque ci si trovi, vai a FINISHED."""
    async with _lock:
        if _phase() == Phase.SETUP or _phase() == Phase.FINISHED:
            return  # gia finito o non iniziato
        STATE.phase = Phase.FINISHED
        STATE.countdown_seconds_left = None


async def admin_seed_demo_questions() -> None:
    """Carica un piccolo pool di domande di test (per Tappa 3, finche
    non c'e l'import Excel). Solo in SETUP/LOBBY."""
    async with _lock:
        if _phase() not in (Phase.SETUP, Phase.LOBBY):
            raise StateError("Le domande si caricano solo in SETUP/LOBBY")
        demo = _DEMO_QUESTIONS
        for q in demo:
            STATE.questions_pool[q.id] = q


async def admin_add_questions(questions: list[Question]) -> int:
    """Aggiunge un blocco di domande al pool. Riassegna gli id partendo
    da max(pool_ids)+1 per evitare collisioni con pool gia caricati.
    Ritorna il numero di domande aggiunte."""
    async with _lock:
        if _phase() not in (Phase.SETUP, Phase.LOBBY):
            raise StateError("Le domande si caricano solo in SETUP/LOBBY")
        next_id = (max(STATE.questions_pool.keys()) + 1) if STATE.questions_pool else 1
        added = 0
        for q in questions:
            new_q = q.model_copy(update={"id": next_id})
            STATE.questions_pool[next_id] = new_q
            next_id += 1
            added += 1
        return added


async def admin_reset() -> None:
    """Reset totale dello stato (utile dopo FINISHED o per debug).

    NB: gli altri moduli devono accedere come state.STATE (attribute lookup),
    NON via 'from state import STATE' (binding fissato all'import).
    """
    async with _lock:
        global STATE
        STATE = GameState()


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
async def team_join(first_name: str, last_name: str, team_name: str) -> tuple[Player, bool]:
    """Aggiunge o riusa un giocatore.

    Dedup su (nome, cognome, team_id) case-insensitive: se esiste gia un
    giocatore con la stessa identita nella stessa squadra, riusa il record
    esistente (marcandolo online) e ritorna `replaced=True`. Il chiamante
    (ws.py) usa quel flag per chiudere la WebSocket vecchia.

    Se la squadra non esiste, viene creata. Il primo giocatore della squadra
    e capitano automatico (semplificazione transitoria; sara sostituito dal
    voto MJ in CAPTAIN_ELECTION).
    """
    async with _lock:
        if _phase() not in (Phase.SETUP, Phase.LOBBY):
            raise StateError("I giocatori possono entrare solo in SETUP/LOBBY")

        if _phase() == Phase.SETUP:
            transition(Phase.LOBBY)

        first_name = first_name.strip()
        last_name = last_name.strip()
        team_name = team_name.strip()

        if not first_name or not last_name or not team_name:
            raise StateError("Nome, cognome e squadra sono tutti obbligatori")

        team = _team_by_name(team_name)
        if team is None:
            if len(STATE.teams) >= 8:
                raise StateError("Massimo 8 squadre")
            team = Team(
                id=_new_id(),
                name=team_name.upper(),
                color=_next_team_color(),
                score=STATE.settings.initial_points,
            )
            STATE.teams[team.id] = team

        # dedup: stesso (nome, cognome, squadra) -> riusa l'id esistente
        existing = _find_player_in_team(first_name, last_name, team.id)
        if existing is not None:
            existing.online = True
            return existing, True

        player = Player(
            id=_new_id(),
            first_name=first_name,
            last_name=last_name,
            team_id=team.id,
        )
        STATE.players[player.id] = player

        if team.captain_id is None:
            team.captain_id = player.id

        return player, False


async def team_leave(player_id: str) -> None:
    """Rimuove il giocatore. Auto-elimina la squadra se rimane vuota,
    riassegna il capitano se necessario.

    In PRE_GAME un capitano non puo uscire (destabilizza la sfida appena
    formata): l'admin deve prima riaprire le elezioni.
    """
    async with _lock:
        if _phase() not in (Phase.SETUP, Phase.LOBBY, Phase.CAPTAIN_ELECTION, Phase.PRE_GAME):
            raise StateError("I giocatori possono uscire solo prima della sfida")
        player = STATE.players.get(player_id)
        if player is None or not player.team_id:
            STATE.players.pop(player_id, None)
            return
        if _phase() == Phase.PRE_GAME:
            team = STATE.teams.get(player.team_id)
            if team and team.captain_id == player.id:
                raise StateError(
                    "Il capitano non puo uscire dopo l'elezione: "
                    "chiedi all'admin di riaprire le elezioni"
                )
        STATE.players.pop(player_id, None)
        team_id = player.team_id
        _cleanup_team_after_member_loss(team_id, player.id)
        if _phase() == Phase.CAPTAIN_ELECTION:
            _recompute_provisional_captain(team_id)


async def team_change_team(player_id: str, target_team_name: str) -> None:
    """Sposta il giocatore in un'altra squadra (esistente o nuova).
    Se la squadra di partenza rimane vuota, viene eliminata."""
    async with _lock:
        if _phase() not in (Phase.SETUP, Phase.LOBBY, Phase.CAPTAIN_ELECTION):
            raise StateError("Cambio squadra solo prima della sfida")
        player = STATE.players.get(player_id)
        if player is None:
            raise StateError("Giocatore non trovato")
        target_team_name = target_team_name.strip()
        if not target_team_name:
            raise StateError("Nome squadra obbligatorio")

        target = _team_by_name(target_team_name)
        if target is None:
            if len(STATE.teams) >= 8:
                raise StateError("Massimo 8 squadre")
            target = Team(
                id=_new_id(),
                name=target_team_name.upper(),
                color=_next_team_color(),
                score=STATE.settings.initial_points,
            )
            STATE.teams[target.id] = target

        if target.id == player.team_id:
            return

        if _find_player_in_team(player.first_name, player.last_name, target.id) is not None:
            raise StateError(f"Esiste gia un omonimo nella squadra {target.name}")

        old_team_id = player.team_id
        player.team_id = target.id
        if target.captain_id is None:
            target.captain_id = player.id

        if old_team_id and old_team_id in STATE.teams:
            _cleanup_team_after_member_loss(old_team_id, player.id)

        if _phase() == Phase.CAPTAIN_ELECTION:
            # i voti del player nella vecchia squadra non hanno piu senso
            old_ballots = STATE.captain_votes.get(old_team_id, {}) if old_team_id else {}
            old_ballots.pop(player.id, None)
            if old_team_id:
                _recompute_provisional_captain(old_team_id)
            _recompute_provisional_captain(target.id)


async def team_edit_self(player_id: str, first_name: str, last_name: str) -> None:
    """Modifica nome/cognome del giocatore (correzione typo)."""
    async with _lock:
        if _phase() not in (Phase.SETUP, Phase.LOBBY, Phase.CAPTAIN_ELECTION, Phase.PRE_GAME):
            raise StateError("Modifica anagrafica solo prima della sfida")
        player = STATE.players.get(player_id)
        if player is None:
            raise StateError("Giocatore non trovato")
        first_name = first_name.strip()
        last_name = last_name.strip()
        if not first_name or not last_name:
            raise StateError("Nome e cognome obbligatori")
        if player.team_id:
            existing = _find_player_in_team(first_name, last_name, player.team_id)
            if existing is not None and existing.id != player.id:
                raise StateError("Esiste gia un omonimo nella tua squadra")
        player.first_name = first_name
        player.last_name = last_name


async def team_vote_captain(voter_id: str, candidate_id: str, grade: int) -> None:
    """Voto Majority Judgment di voter per candidate (entrambi nella stessa squadra).

    grade: int 1..5 (5=Eccellente, 4=Buono, 3=Accettabile, 2=Scarso, 1=Inadeguato).
    Sovrascrive il voto precedente per quella coppia (voter, candidate).
    """
    async with _lock:
        if _phase() != Phase.CAPTAIN_ELECTION:
            raise StateError("Voto capitano attivo solo durante le elezioni")
        if not (MJ_GRADE_MIN <= int(grade) <= MJ_GRADE_MAX):
            raise StateError(f"Voto fuori scala (deve essere {MJ_GRADE_MIN}..{MJ_GRADE_MAX})")
        voter = STATE.players.get(voter_id)
        candidate = STATE.players.get(candidate_id)
        if voter is None or candidate is None:
            raise StateError("Votante o candidato non trovato")
        if voter.team_id is None or voter.team_id != candidate.team_id:
            raise StateError("Si vota solo per i membri della propria squadra")
        STATE.captain_votes.setdefault(voter.team_id, {}).setdefault(voter.id, {})[candidate.id] = int(grade)
        _recompute_provisional_captain(voter.team_id)


async def team_rename_team(player_id: str, new_name: str) -> None:
    """Rinomina la squadra del giocatore.

    - In LOBBY o CAPTAIN_ELECTION: qualsiasi membro puo rinominare.
    - In PRE_GAME: solo il capitano (eletto) puo rinominare.

    Validazione: nome non vuoto e unico (case-insensitive).
    """
    async with _lock:
        if _phase() not in (Phase.LOBBY, Phase.CAPTAIN_ELECTION, Phase.PRE_GAME):
            raise StateError("Rinomina squadra solo prima della sfida")
        player = STATE.players.get(player_id)
        if player is None or not player.team_id:
            raise StateError("Giocatore o squadra non trovati")
        team = STATE.teams.get(player.team_id)
        if team is None:
            raise StateError("Squadra non trovata")
        if _phase() == Phase.PRE_GAME and team.captain_id != player.id:
            raise StateError("Solo il capitano eletto puo rinominare la squadra")
        new_name = new_name.strip()
        if not new_name:
            raise StateError("Nome squadra obbligatorio")
        if new_name.upper() == team.name.upper():
            return
        existing = _team_by_name(new_name)
        if existing is not None and existing.id != team.id:
            raise StateError(f"Esiste gia una squadra chiamata {existing.name}")
        team.name = new_name.upper()


async def team_promote_captain(player_id: str) -> None:
    """Cede la fascia di capitano a un altro membro della stessa squadra."""
    async with _lock:
        if _phase() not in (Phase.LOBBY,):
            raise StateError("La fascia di capitano si cambia solo in LOBBY")
        player = STATE.players.get(player_id)
        if player is None or player.team_id is None:
            raise StateError("Giocatore o squadra non trovati")
        team = STATE.teams[player.team_id]
        team.captain_id = player.id


async def team_vote(player_id: str, option: Letter) -> None:
    """Voto del membro non-capitano: si registra nel round corrente.
    Visibile (a tutti per ora; in tappa 4 sara routing scoped al solo capitano)."""
    async with _lock:
        if _phase() != Phase.TURN_QUESTION:
            raise StateError("I voti dei membri solo durante TURN_QUESTION")
        round_ = STATE.current_round
        if round_ is None:
            raise StateError("Nessun round in corso")
        player = STATE.players.get(player_id)
        if player is None:
            raise StateError("Giocatore sconosciuto")
        # solo i membri non-capitani della squadra che deve rispondere votano
        target = round_.target
        if target == "open":
            # open: tutti i membri (non capitani) di tutte le squadre tranne chi pone
            if player.team_id == round_.asking_team_id:
                raise StateError("Chi ha posto la domanda non puo votare")
        else:
            if player.team_id != target:
                raise StateError("Solo la squadra che deve rispondere puo votare")
        team = STATE.teams.get(player.team_id) if player.team_id else None
        if team and team.captain_id == player_id:
            raise StateError("Il capitano risponde, non vota")
        round_.member_votes[player_id] = option


# ============================================================
# Handlers — eventi capitano (durante il turno)
# ============================================================
async def captain_choose_question(captain_id: str, question_id: int, bet: int, target: str) -> int:
    """TURN_CHOICE -> TURN_QUESTION: capitano sceglie domanda + puntata + target.
    Ritorna i secondi totali del countdown calcolati dalla difficolta."""
    async with _lock:
        if _phase() != Phase.TURN_CHOICE:
            raise StateError("Scelta domanda solo in TURN_CHOICE")

        round_ = STATE.current_round
        if round_ is None:
            raise StateError("Nessun round aperto")

        asking = STATE.teams[round_.asking_team_id]
        if asking.captain_id != captain_id:
            raise StateError("Solo il capitano della squadra di turno puo scegliere")

        if question_id in STATE.used_question_ids:
            raise StateError("Domanda gia usata in questa sfida")
        question = STATE.questions_pool.get(question_id)
        if question is None:
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

        seconds = s.seconds_for_difficulty(question.difficulty)

        round_.question_id = question_id
        round_.bet = bet
        round_.target = target
        round_.seconds_total = seconds
        STATE.used_question_ids.add(question_id)
        STATE.countdown_seconds_left = seconds

        transition(Phase.TURN_QUESTION)
        return seconds


async def captain_answer(captain_id: str, option: Letter) -> None:
    """TURN_QUESTION -> TURN_REVEAL: il primo capitano che risponde blocca.
    Applica scoring corretto in base a target e correttezza."""
    async with _lock:
        if _phase() != Phase.TURN_QUESTION:
            raise StateError("Risposta solo in TURN_QUESTION")
        round_ = STATE.current_round
        if round_ is None or round_.answer_letter is not None:
            raise StateError("Round non valido o gia risposto")

        team = _team_of_captain(captain_id)
        if team is None:
            raise StateError("Solo i capitani possono rispondere")
        if team.id == round_.asking_team_id:
            raise StateError("Non puoi rispondere a una domanda che ha posto la tua squadra")

        # validazione target: se la domanda e indirizzata a una squadra specifica,
        # solo quella puo rispondere
        if round_.target != "open" and round_.target != team.id:
            raise StateError("Questa domanda e indirizzata a un'altra squadra")

        question = STATE.questions_pool[round_.question_id]
        is_correct = option == question.correct
        round_.answer_letter = option
        round_.answer_team_id = team.id
        round_.is_correct = is_correct
        STATE.countdown_seconds_left = None

        _apply_scoring_for_answer(round_, team.id, is_correct)
        transition(Phase.TURN_REVEAL)


# ============================================================
# Countdown / timeout
# ============================================================
async def tick_countdown() -> str:
    """Chiamato dall'orchestrator (ws.py) ogni 1s in TURN_QUESTION.
    Ritorna:
      - "tick"    : decrementato, bisogna fare broadcast countdown/tick
      - "timeout" : countdown a 0, applicato scoring di timeout, transition in REVEAL
                    bisogna fare broadcast state/full
      - "stopped" : fase diversa da TURN_QUESTION, fermarsi (no broadcast)
    """
    async with _lock:
        if _phase() != Phase.TURN_QUESTION:
            return "stopped"
        if STATE.countdown_seconds_left is None:
            return "stopped"
        STATE.countdown_seconds_left -= 1
        if STATE.countdown_seconds_left > 0:
            return "tick"
        STATE.countdown_seconds_left = 0
        round_ = STATE.current_round
        if round_ is None:
            return "stopped"
        round_.timed_out = True
        _apply_scoring_for_timeout(round_)
        STATE.countdown_seconds_left = None
        transition(Phase.TURN_REVEAL)
        return "timeout"


# ============================================================
# Scoring
# ============================================================
def _apply_scoring_for_answer(round_: Round, answering_team_id: str, is_correct: bool) -> None:
    """Applica i punti dopo che una squadra ha risposto (corretto/sbagliato)."""
    bet = round_.bet or 0
    asking_id = round_.asking_team_id
    delta: dict[str, int] = {}

    if round_.target == "open":
        # aperta a tutti: chi risponde
        if is_correct:
            #  vince bet -> chi ha posto perde bet
            delta[answering_team_id] = +bet
            delta[asking_id] = -bet
        else:
            #  perde bet -> chi ha posto vince bet
            delta[answering_team_id] = -bet
            delta[asking_id] = +bet
    else:
        # target: la squadra X (la stessa che ha risposto, validato a monte)
        if is_correct:
            # X vince bet, chi ha posto perde bet
            delta[answering_team_id] = +bet
            delta[asking_id] = -bet
        else:
            # X perde bet, chi ha posto vince bet
            delta[answering_team_id] = -bet
            delta[asking_id] = +bet

    _commit_delta(round_, delta)


def _apply_scoring_for_timeout(round_: Round) -> None:
    """Applica i punti se il countdown scade senza risposta.

    Regole:
    - target = team X: X perde bet, chi ha posto vince bet
    - target = open: tutte le squadre (tranne chi pone) perdono bet/(N-1)
                     (troncato all'unita); il totale va a chi pone
    """
    bet = round_.bet or 0
    asking_id = round_.asking_team_id
    delta: dict[str, int] = {}

    if round_.target == "open":
        others = [tid for tid in STATE.teams if tid != asking_id]
        n = len(others)
        if n == 0:
            return
        share = bet // n  # troncato come da specifica
        total_to_asker = 0
        for tid in others:
            delta[tid] = -share
            total_to_asker += share
        delta[asking_id] = +total_to_asker
    else:
        target = round_.target
        delta[target] = -bet
        delta[asking_id] = +bet

    _commit_delta(round_, delta)


def _commit_delta(round_: Round, delta: dict[str, int]) -> None:
    """Applica delta ai score delle squadre e lo registra nel round."""
    round_.points_delta.clear()
    for team_id, d in delta.items():
        if team_id in STATE.teams:
            STATE.teams[team_id].score += d
        round_.points_delta[team_id] = d


# ============================================================
# Demo seed (Tappa 3, finche non c'e l'import Excel)
# ============================================================
def _q(id_: int, lecture: str, topic: str, text: str, opts: list[str], correct: str, difficulty: int) -> Question:
    options = [QuestionOption(letter=l, text=t) for l, t in zip(["A", "B", "C", "D"], opts)]
    return Question(
        id=id_, lecture=lecture, topic=topic, text=text,
        options=options, correct=correct, difficulty=difficulty,
    )


_DEMO_QUESTIONS = [
    _q(1, "L11", "CAD", "Che cosa stabilisce l'art. 64-bis del CAD per l'accesso ai servizi digitali della PA?",
       ["Accesso libero senza autenticazione",
        "Accesso tramite SPID, CIE o CNS come unici strumenti consentiti",
        "Accesso libero ma con tracciamento IP",
        "Accesso solo per chi ha PEC attiva"], "B", 2),
    _q(2, "L11", "SPID", "A cosa serve SPID di livello 2?",
       ["Servizi a basso rischio anonimi",
        "Accesso con username + password + OTP",
        "Firma digitale qualificata",
        "Solo identificazione cartacea"], "B", 1),
    _q(3, "L09", "GDPR", "Quando il titolare puo trasferire dati verso un Paese terzo senza decisione di adeguatezza?",
       ["Mai",
        "Sempre, basta il consenso",
        "Con garanzie adeguate (clausole tipo, BCR) o deroghe",
        "Solo dentro l'UE"], "C", 3),
    _q(4, "L05", "eIDAS", "Quale firma elettronica garantisce l'identificazione univoca del firmatario?",
       ["Firma elettronica semplice",
        "Firma elettronica avanzata",
        "Firma elettronica qualificata",
        "Firma autografa digitalizzata"], "C", 2),
    _q(5, "L13", "PDND", "Qual e la differenza tra Catalogo API e Voucher nella PDND?",
       ["Sono la stessa cosa",
        "Catalogo elenca le API, Voucher autorizza l'accesso",
        "Catalogo e per i privati, Voucher per la PA",
        "Voucher elenca i prezzi"], "B", 2),
    _q(6, "L02", "GAN", "In una GAN, qual e il ruolo del generatore?",
       ["Classificare immagini reali",
        "Produrre dati falsi che ingannino il discriminatore",
        "Solo addestrare il discriminatore",
        "Etichettare i dataset"], "B", 3),
]
