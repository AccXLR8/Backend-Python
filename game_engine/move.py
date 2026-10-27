"""Lightweight move representation."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Move:
    row: int
    col: int
