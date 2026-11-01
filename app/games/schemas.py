from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.games.models import GameStatus, FinishReason


class CreateGameRequest(BaseModel):
    board_width: int = Field(default=8, ge=3, le=16)
    board_height: int = Field(default=8, ge=3, le=16)
    clock_seconds: int = Field(default=300, ge=60, le=3600)


class GamePlayerSchema(BaseModel):
    user_id: uuid.UUID
    username: str
    player_slot: int
    connected: bool
    time_remaining_ms: int
    orb_skin_id: str = "classic"
    nationality: str = "global"


class GameResponse(BaseModel):
    id: uuid.UUID
    status: GameStatus
    is_ranked: bool = False
    players: List[GamePlayerSchema]
    turn_number: int
    current_player_slot: Optional[int]
    created_at: datetime
    updated_at: datetime


class JoinGameRequest(BaseModel):
    player_slot: Optional[int] = None


class MoveRequest(BaseModel):
    cell: int
    client_move_id: Optional[str] = None


class MoveResponse(BaseModel):
    sequence: int
    turn_number: int
    player_slot: int
    current_player_slot: Optional[int] = None
    clocks: dict[str, int] = Field(default_factory=dict)
    reaction: dict
    final_state: dict
    status: GameStatus


class WebSocketTicketResponse(BaseModel):
    ticket: str
    expires_in: int = 30
    subprotocol: str = "chainreaction-v1"


class GameHistoryResponse(BaseModel):
    game_id: uuid.UUID
    moves: List[dict]
    result: Optional[FinishReason]
