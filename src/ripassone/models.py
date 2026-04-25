"""Modelli Pydantic per Il Ripassone.

Le entita di gioco e lo stato globale. Tutto serializzabile in JSON
per inviare snapshot completi via WebSocket.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Letter = Literal["A", "B", "C", "D"]


class Phase(str, Enum):
    """Stati della state machine di gioco."""
    SETUP = "setup"                 # admin sta configurando
    LOBBY = "lobby"                 # studenti si connettono e formano squadre
    READY_TO_START = "ready_to_start"  # ordine sorteggiato, in attesa start
    TURN_CHOICE = "turn_choice"     # capitano di turno sceglie domanda
    TURN_QUESTION = "turn_question" # countdown attivo, in attesa risposta
    TURN_REVEAL = "turn_reveal"     # risposta rivelata, scoring fatto
    FINISHED = "finished"           # quiz concluso, classifica finale


class Settings(BaseModel):
    """Parametri del quiz definiti dall'admin in fase SETUP."""
    rounds: int = 12
    seconds: int = 30
    initial_points: int = 100
    min_bet: int = 5
    max_bet: int = 50


class Player(BaseModel):
    id: str
    first_name: str
    last_name: str
    team_id: str | None = None
    online: bool = True

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class Team(BaseModel):
    id: str
    name: str
    color: str
    captain_id: str | None = None
    score: int = 0


class QuestionOption(BaseModel):
    letter: Letter
    text: str


class Question(BaseModel):
    id: int
    lecture: str
    topic: str
    text: str
    options: list[QuestionOption]
    correct: Letter
    difficulty: int = 1
    source: str | None = None
    author: str | None = None  # studente che ha proposto la domanda


class Round(BaseModel):
    """Un singolo turno di gioco. Si crea entrando in TURN_CHOICE
    e si chiude entrando in TURN_REVEAL (o nel passaggio successivo)."""
    number: int
    asking_team_id: str
    question_id: int | None = None
    bet: int | None = None
    target: str | None = None  # "open" oppure team_id
    answer_letter: Letter | None = None
    answer_team_id: str | None = None  # squadra che ha risposto (per buzzer/aperta)
    points_delta: dict[str, int] = Field(default_factory=dict)  # team_id -> +/- pt


class GameState(BaseModel):
    """Stato globale del gioco. Vive in RAM per la durata della partita."""
    model_config = ConfigDict(use_enum_values=True)

    phase: Phase = Phase.SETUP
    settings: Settings = Field(default_factory=Settings)

    players: dict[str, Player] = Field(default_factory=dict)
    teams: dict[str, Team] = Field(default_factory=dict)

    questions_pool: dict[int, Question] = Field(default_factory=dict)
    used_question_ids: set[int] = Field(default_factory=set)

    turn_order: list[str] = Field(default_factory=list)  # team_ids sorteggiati
    current_turn_idx: int = 0  # indice in turn_order della squadra che pone

    rounds: list[Round] = Field(default_factory=list)
    countdown_seconds_left: int | None = None

    @property
    def current_round(self) -> Round | None:
        return self.rounds[-1] if self.rounds else None

    @property
    def asking_team_id(self) -> str | None:
        if not self.turn_order:
            return None
        return self.turn_order[self.current_turn_idx % len(self.turn_order)]
