from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, is_dataclass
from importlib import import_module
from typing import Any, Iterable, Protocol

from app.engine.models import EngineGameState, EngineMove, EngineMoveResult


logger = logging.getLogger(__name__)


class EngineAdapter(Protocol):
    async def new_game_state(self, width: int, height: int, players: Iterable[int]) -> EngineGameState:
        ...

    async def apply_move(self, state: EngineGameState, player_slot: int, move: EngineMove) -> EngineMoveResult:
        ...


class EngineIllegalMoveError(RuntimeError):
    """Raised when the underlying engine rejects a move as illegal."""


class ChainReactionPyEngineAdapter:
    """Adapter that bridges the pure-Python `chainreaction_py` engine into the backend."""

    def __init__(self, module_name: str = "chainreaction_py") -> None:
        self.module_name = module_name
        self._module = None
        self._move_cls = None
        self._game_state_cls = None
        self._illegal_move_cls = None

    def _ensure_module(self):
        if self._module is None:
            module = import_module(self.module_name)
            self._module = module
            self._move_cls = getattr(module, "Move", None)
            self._game_state_cls = getattr(module, "GameState", None)
            illegal_cls = getattr(module, "IllegalMoveError", None)
            if illegal_cls is None:
                errors_mod = getattr(module, "errors", None)
                if errors_mod is not None:
                    illegal_cls = getattr(errors_mod, "IllegalMoveError", None)
            self._illegal_move_cls = illegal_cls
        return self._module

    def _game_state_to_payload(self, state: Any) -> dict[str, Any]:
        if state is None:
            raise ValueError("Engine returned an empty state")
        if hasattr(state, "to_dict"):
            return state.to_dict()
        if hasattr(state, "to_json"):
            return json.loads(state.to_json())
        if is_dataclass(state):
            return asdict(state)
        if isinstance(state, dict):
            return state
        raise TypeError(f"Cannot serialize engine state of type {type(state)!r}")

    def _game_state_from_payload(self, payload: dict[str, Any]):
        module = self._ensure_module()
        game_state_cls = self._game_state_cls or getattr(module, "GameState", None)
        if game_state_cls is None:
            raise RuntimeError("The engine module does not expose a GameState class")
        if hasattr(game_state_cls, "from_dict"):
            return game_state_cls.from_dict(payload)
        if hasattr(game_state_cls, "from_json"):
            return game_state_cls.from_json(json.dumps(payload))
        return game_state_cls(**payload)

    def _to_plain(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {k: self._to_plain(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._to_plain(v) for v in value]
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if is_dataclass(value):
            return asdict(value)
        return value

    async def new_game_state(self, width: int, height: int, players: Iterable[int]) -> EngineGameState:
        module = self._ensure_module()

        def _create() -> dict[str, Any]:
            state = module.new_game(width=width, height=height, players=list(players))
            payload = self._game_state_to_payload(state)
            logger.info(
                "engine.new_game_state",
                extra={
                    "width": width,
                    "height": height,
                    "players": list(players),
                    "payload_bytes": len(json.dumps(payload)),
                },
            )
            return payload

        data = await asyncio.to_thread(_create)
        return EngineGameState(data=data)

    async def apply_move(self, state: EngineGameState, player_slot: int, move: EngineMove) -> EngineMoveResult:
        module = self._ensure_module()
        move_cls = self._move_cls or getattr(module, "Move", None)
        if move_cls is None:
            raise RuntimeError("The engine module does not expose a Move class")

        def _apply() -> tuple[dict[str, Any], dict[str, Any] | list[Any] | None, dict[str, Any] | None, int, int]:
            cr_state = self._game_state_from_payload(state.data)
            cr_move = move_cls(row=move.row, col=move.col)
            try:
                result = module.apply_move(cr_state, player=player_slot, mv=cr_move)
            except Exception as exc:  # pragma: no cover - depends on engine runtime
                illegal_cls = self._illegal_move_cls
                if illegal_cls and isinstance(exc, illegal_cls):
                    raise EngineIllegalMoveError(str(exc)) from exc
                raise
            final_state = self._game_state_to_payload(result.final_state)
            raw_reaction = self._to_plain(getattr(result, "timeline", None))
            reaction = raw_reaction if raw_reaction is not None else {}
            raw_outcome = self._to_plain(getattr(result, "outcome", None))
            outcome = raw_outcome if raw_outcome is not None else {}
            payload_bytes = len(
                json.dumps(
                    {
                        "final_state": final_state,
                        "timeline": reaction,
                        "outcome": outcome,
                    }
                )
            )
            timeline_steps = 0
            if isinstance(reaction, dict):
                steps = reaction.get("steps")
                if isinstance(steps, list):
                    timeline_steps = len(steps)
            return final_state, reaction, outcome, payload_bytes, timeline_steps

        final_state_payload, reaction_payload, outcome_payload, payload_bytes, timeline_steps = await asyncio.to_thread(_apply)
        logger.info(
            "engine.apply_move",
            extra={
                "player_slot": player_slot,
                "move_row": move.row,
                "move_col": move.col,
                "payload_bytes": payload_bytes,
                "timeline_steps": timeline_steps,
            },
        )
        final_state = EngineGameState(data=final_state_payload)
        return EngineMoveResult(
            final_state=final_state,
            reaction=reaction_payload,
            result=outcome_payload,
        )


class FakeEngineAdapter:
    """Deterministic fake for tests and local development."""

    async def new_game_state(self, width: int, height: int, players: Iterable[int]) -> EngineGameState:
        board = [[None for _ in range(width)] for _ in range(height)]
        data = {
            "width": width,
            "height": height,
            "board": board,
            "players": list(players),
            "turn_number": 0,
            "current_player": 0,
        }
        return EngineGameState(data=data)

    async def apply_move(self, state: EngineGameState, player_slot: int, move: EngineMove) -> EngineMoveResult:
        data = json.loads(json.dumps(state.data))
        history = data.setdefault("history", [])
        history.append({"row": move.row, "col": move.col, "player": player_slot})
        data["turn_number"] = data.get("turn_number", 0) + 1
        data["current_player"] = 1 - player_slot
        return EngineMoveResult(
            final_state=EngineGameState(data),
            reaction={"steps": []},
            result={"status": "ACTIVE"},
        )
