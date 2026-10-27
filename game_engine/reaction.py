"""Reaction timeline data structures."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(slots=True)
class OrbPlacement:
    cell: int
    player: int


@dataclass(slots=True)
class Explosion:
    cell: int
    player: int


@dataclass(slots=True)
class OrbTransfer:
    from_cell: int
    to_cell: int
    player: int


@dataclass(slots=True)
class ReactionStep:
    explosions: List[Explosion] = field(default_factory=list)
    transfers: List[OrbTransfer] = field(default_factory=list)


@dataclass(slots=True)
class ReactionTimeline:
    placement: OrbPlacement
    steps: List[ReactionStep] = field(default_factory=list)

    def is_empty(self) -> bool:
        return len(self.steps) == 0
