"""Pure Python Chain Reaction engine implemented on top of NumPy."""
from __future__ import annotations

from typing import List, Optional, Sequence

from .board import Board
from .errors import (
    IllegalMove,
    InvalidState,
    ReactionCycle,
    ReactionError,
    ReactionLimitExceeded,
    ReplayError,
)
from .game_state import GameState, GameStatus, PlayerState
from .move import Move
from .reaction import Explosion, OrbPlacement, OrbTransfer, ReactionStep, ReactionTimeline
from .result import GameResult, MoveResult


def new_game(width: int, height: int, players: Sequence[int]) -> GameState:
    if len(players) < 2:
        raise InvalidState("game has no players")
    if len(set(players)) != len(players):
        raise InvalidState("player order invalid")
    board = Board.new(width, height)
    player_states = [PlayerState(id=p) for p in players]
    return GameState(board=board, players=player_states)


def validate_move(state: GameState, player: int, mv: Move) -> None:
    if state.status.kind != "active":
        raise IllegalMove.game_already_finished()
    if state.current_player() != player:
        raise IllegalMove.not_players_turn()
    player_entry = next((p for p in state.players if p.id == player), None)
    if player_entry is None or player_entry.eliminated:
        raise IllegalMove.player_eliminated()
    if not (0 <= mv.row < state.board.height and 0 <= mv.col < state.board.width):
        raise IllegalMove.out_of_bounds()
    owner = state.board.owner_at(mv.row, mv.col)
    if owner not in (-1, player):
        raise IllegalMove.cell_owned_by_opponent(owner)


def apply_move(state: GameState, player: int, mv: Move) -> MoveResult:
    validate_move(state, player, mv)
    next_state = state.copy()
    next_state.turn_number = (next_state.turn_number + 1) & 0xFFFF_FFFF_FFFF_FFFF
    next_state.board.add_orb(mv.row, mv.col, player)
    _mark_player_moved(next_state, player)

    placement = OrbPlacement(cell=next_state.board.index(mv.row, mv.col), player=player)
    timeline = ReactionTimeline(placement=placement)
    initial_unstable: List[int] = []
    if next_state.board.orb_count_at(mv.row, mv.col) >= next_state.board.critical_mass(mv.row, mv.col):
        initial_unstable.append(placement.cell)

    outcome = _simulate_reactions(next_state, timeline, initial_unstable)
    if outcome is None:
        _update_eliminations(next_state)
        outcome = _evaluate_result(next_state)
    if next_state.status.kind == "active":
        _advance_turn(next_state)
    return MoveResult(next_state, timeline, outcome)


def replay(initial_state: GameState, moves: Sequence[Move]) -> GameState:
    state = initial_state.copy()
    for idx, mv in enumerate(moves):
        player = state.current_player()
        try:
            result = apply_move(state, player, mv)
        except (IllegalMove, ReactionError) as err:
            raise ReplayError(f"move failed at step {idx}: {err}") from err
        state = result.final_state
    return state


def _mark_player_moved(state: GameState, player: int) -> None:
    for entry in state.players:
        if entry.id == player:
            entry.has_moved = True
            return
    raise InvalidState("player order invalid")


def _simulate_reactions(
    state: GameState, timeline: ReactionTimeline, current_wave: List[int]
) -> Optional[GameResult]:
    """Resolve reaction waves, stopping as soon as the game is won.

    Explosions intentionally clear the entire cell and transfer one orb to each
    orthogonal neighbor. Repeated-state detection and a hard explosion limit
    protect callers from pathological reactions that cannot settle.
    """
    if not current_wave:
        return None

    board = state.board
    width = board.width
    seen_states: set[tuple[bytes, bytes, tuple[int, ...]]] = set()
    explosion_count = 0
    explosion_limit = max(10_000, board.len * 1_000)

    while current_wave:
        current_wave.sort()
        signature = (
            board.owners.tobytes(),
            board.orbs.tobytes(),
            tuple(current_wave),
        )
        if signature in seen_states:
            raise ReactionCycle("reaction entered a repeating state")
        seen_states.add(signature)

        step = ReactionStep()
        next_wave: List[int] = []
        next_flags = [False] * board.len
        for cell_index in current_wave:
            row, col = divmod(cell_index, width)
            owner = board.owner_at(row, col)
            if owner == -1:
                continue

            explosion_count += 1
            if explosion_count > explosion_limit:
                raise ReactionLimitExceeded(
                    f"reaction exceeded {explosion_limit} explosions"
                )

            step.explosions.append(Explosion(cell=cell_index, player=owner))
            board.drain_cell(row, col)
            for n_row, n_col in board.neighbors(row, col):
                board.add_orb(n_row, n_col, owner)
                neighbor_index = board.index(n_row, n_col)
                step.transfers.append(
                    OrbTransfer(from_cell=cell_index, to_cell=neighbor_index, player=owner)
                )
                if (
                    board.orb_count_at(n_row, n_col) >= board.critical_mass(n_row, n_col)
                    and not next_flags[neighbor_index]
                ):
                    next_flags[neighbor_index] = True
                    next_wave.append(neighbor_index)

        timeline.steps.append(step)

        # A player who has moved and owns no cells cannot regain ownership after
        # a complete wave. End immediately once only one living player remains,
        # rather than continuing winner-only reactions indefinitely.
        _update_eliminations(state)
        outcome = _evaluate_result(state)
        if outcome.winner is not None:
            return outcome

        current_wave = next_wave

    return None


def _update_eliminations(state: GameState) -> None:
    for player in state.players:
        if player.eliminated or not player.has_moved:
            continue
        if state.board.owner_cell_count(player.id) == 0:
            player.eliminated = True


def _evaluate_result(state: GameState) -> GameResult:
    living = [p.id for p in state.players if not p.eliminated]
    if len(living) == 1:
        winner = living[0]
        losers = [p.id for p in state.players if p.id != winner]
        state.status = GameStatus.won(winner, losers.copy())
        return GameResult.won(winner, losers)
    state.status = GameStatus.active()
    return GameResult.active()


def _advance_turn(state: GameState) -> None:
    player_count = len(state.players)
    start = state.current_player_index
    for offset in range(1, player_count + 1):
        idx = (start + offset) % player_count
        if not state.players[idx].eliminated:
            state.current_player_index = idx
            return
    state.current_player_index = start
