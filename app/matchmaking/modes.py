from __future__ import annotations

from enum import Enum


class MatchmakingMode(str, Enum):
    """Supported matchmaking queues mapped to board presets."""

    STANDARD = "standard"
    MINI = "mini"
