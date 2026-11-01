from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class GameStatus(str, enum.Enum):
    WAITING = "WAITING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    FINISHED = "FINISHED"
    ABANDONED = "ABANDONED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class FinishReason(str, enum.Enum):
    NORMAL = "NORMAL"
    RESIGNATION = "RESIGNATION"
    TIMEOUT = "TIMEOUT"
    ABANDONED = "ABANDONED"
    ERROR = "ERROR"
    DISCONNECT = "DISCONNECT"


@dataclass
class PlayerClock:
    player_slot: int
    remaining_ms: int
    running_since: Optional[float] = None

    def start(self, now: float) -> None:
        if self.running_since is None:
            self.running_since = now

    def stop(self, now: float) -> None:
        if self.running_since is not None:
            elapsed = int((now - self.running_since) * 1000)
            self.remaining_ms = max(0, self.remaining_ms - elapsed)
            self.running_since = None

    def snapshot(self, now: float) -> int:
        if self.running_since is None:
            return self.remaining_ms
        elapsed = int((now - self.running_since) * 1000)
        return max(0, self.remaining_ms - elapsed)


@dataclass
class GameParticipant:
    user_id: str
    player_slot: int
    connected: bool = True
    client_version: Optional[str] = None


@dataclass
class GameConfig:
    board_width: int
    board_height: int
    initial_time_ms: int


@dataclass
class GameStateSnapshot:
    game_id: str
    status: GameStatus
    sequence: int
    turn_number: int
    current_player_slot: Optional[int]
    board_state: dict
    participants: list[GameParticipant]
    clocks: dict[int, int]
    updated_at: datetime
