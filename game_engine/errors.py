"""Engine-specific exception hierarchy mirroring the Rust enums."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class EngineError(Exception):
    """Base class for all engine errors."""


@dataclass(slots=True)
class IllegalMove(EngineError):
    kind: str
    detail: Optional[dict] = None

    @classmethod
    def game_already_finished(cls) -> "IllegalMove":
        return cls("GameAlreadyFinished")

    @classmethod
    def not_players_turn(cls) -> "IllegalMove":
        return cls("NotPlayersTurn")

    @classmethod
    def player_eliminated(cls) -> "IllegalMove":
        return cls("PlayerEliminated")

    @classmethod
    def out_of_bounds(cls) -> "IllegalMove":
        return cls("OutOfBounds")

    @classmethod
    def cell_owned_by_opponent(cls, owner: int) -> "IllegalMove":
        return cls("CellOwnedByOpponent", {"owner": owner})


class InvalidState(EngineError):
    pass


class InvalidBoard(EngineError):
    pass


class ReplayError(EngineError):
    pass


class ReactionError(EngineError):
    """Base class for reactions that cannot be resolved safely."""


class ReactionCycle(ReactionError):
    """Raised when a deterministic reaction state repeats."""


class ReactionLimitExceeded(ReactionError):
    """Raised when a reaction exceeds its defensive explosion limit."""
