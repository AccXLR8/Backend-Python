"""Move/result data structures."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .game_state import GameState, GameStatus
from .reaction import ReactionTimeline


@dataclass(slots=True)
class GameResult:
    status: GameStatus
    winner: Optional[int]
    losers: List[int]

    @staticmethod
    def active() -> "GameResult":
        return GameResult(GameStatus.active(), None, [])

    @staticmethod
    def won(winner: int, losers: List[int]) -> "GameResult":
        return GameResult(GameStatus.won(winner, losers), winner, losers)


@dataclass(slots=True)
class MoveResult:
    final_state: GameState
    timeline: ReactionTimeline
    outcome: GameResult
