"""Python equivalents of the engine's state structs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .board import Board


@dataclass(slots=True)
class PlayerState:
    id: int
    eliminated: bool = False
    has_moved: bool = False


@dataclass(slots=True)
class GameStatus:
    kind: str
    winner: Optional[int] = None
    eliminated: List[int] = field(default_factory=list)

    @staticmethod
    def active() -> "GameStatus":
        return GameStatus("active")

    @staticmethod
    def won(winner: int, eliminated: List[int]) -> "GameStatus":
        return GameStatus("won", winner=winner, eliminated=eliminated)


@dataclass(slots=True)
class GameState:
    board: Board
    players: List[PlayerState]
    current_player_index: int = 0
    turn_number: int = 0
    status: GameStatus = field(default_factory=GameStatus.active)

    def copy(self) -> "GameState":
        return GameState(
            board=self.board.copy(),
            players=[PlayerState(p.id, p.eliminated, p.has_moved) for p in self.players],
            current_player_index=self.current_player_index,
            turn_number=self.turn_number,
            status=GameStatus(self.status.kind, self.status.winner, list(self.status.eliminated)),
        )

    def current_player(self) -> int:
        return self.players[self.current_player_index].id

    def living_players(self) -> List[int]:
        return [p.id for p in self.players if not p.eliminated]

    def to_dict(self) -> dict:
        return {
            "board": {
                "width": self.board.width,
                "height": self.board.height,
                "cells": self.board.snapshot(),
            },
            "players": [
                {
                    "id": p.id,
                    "eliminated": p.eliminated,
                    "has_moved": p.has_moved,
                }
                for p in self.players
            ],
            "current_player_index": self.current_player_index,
            "turn_number": self.turn_number,
            "status": {
                "kind": self.status.kind,
                "winner": self.status.winner,
                "eliminated": list(self.status.eliminated),
            },
        }
