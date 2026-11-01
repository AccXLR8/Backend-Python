from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class User:
    id: str
    username: str
    created_at: datetime
    updated_at: datetime

    is_active: bool = True
    is_bot: bool = False
    is_guest: bool = False
    nationality: str = "global"
