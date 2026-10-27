import numpy as np
import pytest

from game_engine import (
    GameState,
    Move,
    PlayerState,
    ReactionCycle,
    apply_move,
    new_game,
)
from game_engine.board import Board


def _state_from_cells(cells, players):
    """Build a stable test state from (owner, orb_count) cells."""
    height = len(cells)
    width = len(cells[0])
    board = Board.new(width, height)
    for row, row_cells in enumerate(cells):
        for col, cell in enumerate(row_cells):
            if cell is None:
                continue
            owner, orb_count = cell
            board.owners[row, col] = owner
            board.orbs[row, col] = orb_count
    return GameState(board=board, players=players)


def test_reaction_stops_immediately_when_last_opponent_is_captured():
    state = _state_from_cells(
        [
            [(0, 1), (0, 1)],
            [(0, 1), (1, 1)],
        ],
        [PlayerState(0, has_moved=True), PlayerState(1, has_moved=True)],
    )

    result = apply_move(state, 0, Move(0, 0))

    assert result.outcome.winner == 0
    assert result.final_state.status.kind == "won"
    assert result.final_state.players[1].eliminated is True
    assert result.final_state.board.owner_cell_count(1) == 0
    # Victory occurs after the second wave; winner-only reactions are not run.
    assert len(result.timeline.steps) == 2


def test_repeating_reaction_raises_controlled_error_without_mutating_input():
    # On a 2x2 board this position alternates between unstable diagonals after
    # the bottom-right placement. Player 1 has not moved, so cannot yet be
    # eliminated; repeated-state detection must stop the reaction safely.
    state = _state_from_cells(
        [
            [None, (0, 1)],
            [(0, 1), (0, 1)],
        ],
        [PlayerState(0, has_moved=True), PlayerState(1, has_moved=False)],
    )
    owners_before = state.board.owners.copy()
    orbs_before = state.board.orbs.copy()

    with pytest.raises(ReactionCycle):
        apply_move(state, 0, Move(1, 1))

    np.testing.assert_array_equal(state.board.owners, owners_before)
    np.testing.assert_array_equal(state.board.orbs, orbs_before)
    assert state.turn_number == 0
    assert state.status.kind == "active"


def test_non_reacting_move_retains_normal_turn_behavior():
    state = new_game(6, 8, [0, 1])

    result = apply_move(state, 0, Move(2, 3))

    assert result.timeline.is_empty()
    assert result.final_state.current_player() == 1
    assert result.final_state.turn_number == 1
    assert result.outcome.winner is None
