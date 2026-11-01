from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import WebSocket


@dataclass
class WebSocketConnection:
    connection_id: str
    websocket: WebSocket
    user_id: str
    game_id: str
    player_slot: int
    connected_at: datetime
    last_heartbeat: Optional[datetime] = None
