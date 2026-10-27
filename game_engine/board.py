"""NumPy-backed board representation for the Chain Reaction game."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, List, Tuple

import numpy as np

from .errors import InvalidBoard


@dataclass(slots=True)
class Board:
    width: int
    height: int
    owners: np.ndarray
    orbs: np.ndarray

    @classmethod
    def new(cls, width: int, height: int) -> "Board":
        if width <= 0 or height <= 0:
            raise InvalidBoard("board dimension cannot be zero")
        if width < 2 or height < 2:
            raise InvalidBoard("board dimension must be at least 2x2")
        owners = np.full((height, width), fill_value=-1, dtype=np.int16)
        orbs = np.zeros((height, width), dtype=np.int32)
        return cls(width, height, owners, orbs)

    def copy(self) -> "Board":
        return Board(self.width, self.height, self.owners.copy(), self.orbs.copy())

    @property
    def len(self) -> int:
        return self.width * self.height

    def index(self, row: int, col: int) -> int:
        if not (0 <= row < self.height and 0 <= col < self.width):
            raise IndexError("cell out of bounds")
        return row * self.width + col

    def coords(self, index: int) -> Tuple[int, int]:
        row = index // self.width
        col = index % self.width
        return row, col

    def owner_at(self, row: int, col: int) -> int:
        return int(self.owners[row, col])

    def orb_count_at(self, row: int, col: int) -> int:
        return int(self.orbs[row, col])

    def add_orb(self, row: int, col: int, player: int) -> None:
        self.owners[row, col] = player
        self.orbs[row, col] += 1

    def drain_cell(self, row: int, col: int) -> int:
        removed = int(self.orbs[row, col])
        self.orbs[row, col] = 0
        self.owners[row, col] = -1
        return removed

    def critical_mass(self, row: int, col: int) -> int:
        count = 0
        if row > 0:
            count += 1
        if row + 1 < self.height:
            count += 1
        if col > 0:
            count += 1
        if col + 1 < self.width:
            count += 1
        return count

    def neighbors(self, row: int, col: int) -> Iterator[Tuple[int, int]]:
        if row > 0:
            yield row - 1, col
        if col + 1 < self.width:
            yield row, col + 1
        if row + 1 < self.height:
            yield row + 1, col
        if col > 0:
            yield row, col - 1

    def owner_cell_count(self, player: int) -> int:
        mask = self.owners == player
        return int(np.count_nonzero(mask & (self.orbs > 0)))

    def snapshot(self) -> List[List[dict | None]]:
        out: List[List[dict | None]] = []
        for row in range(self.height):
            row_data: List[dict | None] = []
            for col in range(self.width):
                owner = int(self.owners[row, col])
                if owner == -1:
                    row_data.append(None)
                else:
                    row_data.append({"owner": owner, "orb_count": int(self.orbs[row, col])})
            out.append(row_data)
        return out
