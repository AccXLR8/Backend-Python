# Chain Reaction Game Engine – Documentation

This document explains the pure-Python Chain Reaction rules package vendored in `game_engine/`.

## 1. Prerequisites

- Python 3.10+
- `pip` and a virtual environment (recommended)

## 2. Installation

The engine is installed with the backend from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .[dev]
```

The import package is named `game_engine`; NumPy is declared by the backend project.

## 3. Quick Start

```python
from game_engine import new_game, apply_move, validate_move, Move

state = new_game(width=6, height=8, players=[0, 1])
move = Move(row=2, col=3)
validate_move(state, player=0, mv=move)
result = apply_move(state, player=0, mv=move)
print("Winner:", result.outcome.winner)
print("Timeline steps:", len(result.timeline.steps))
state = result.final_state  # feed into the next turn
```

## 4. Module Overview

| Module | Responsibility |
| --- | --- |
| `game_engine.board` | NumPy-backed grid storage (owners/orb counts, neighbor lookup). |
| `game_engine.game_state` | Data classes (`GameState`, `GameStatus`, `PlayerState`) with cloning helpers. |
| `game_engine.engine` | Implements `new_game`, `validate_move`, `apply_move`, `replay`, and reaction logic. |
| `game_engine.reaction` | Timeline structs (`ReactionTimeline`, `ReactionStep`, etc.). |
| `game_engine.result` | `MoveResult` and `GameResult` containers. |
| `game_engine.errors` | Exception hierarchy matching the Rust enums. |

## 5. Architecture Highlights

- **State cloning**: `GameState.copy()` deep-copies the board (NumPy arrays) and player list
  to ensure immutability between calls, matching the Rust semantics.
- **Reaction simulation**: `_simulate_reactions` queues unstable cells per wave and records
  explosions/transfers for each step. An exploding cell is cleared completely and sends one
  orb to each orthogonal neighbor; excess mass is intentionally consumed by the explosion.
- **Immediate victory**: Eliminations are evaluated after every completed reaction wave. As
  soon as only one eligible player remains, the game ends without running winner-only waves.
- **Termination safeguards**: Exact repeated reaction states raise `ReactionCycle`; a
  defensive limit of `max(10,000, board_cells * 1,000)` explosions raises
  `ReactionLimitExceeded`. Since moves operate on clones, either error leaves the caller's
  input state unchanged.
- **Outcome detection**: `_evaluate_result` updates `GameStatus` (active vs. won) both during
  reactions and after ordinary non-reacting moves.

## 6. Testing

Regression tests are included under `tests/`. After installing with
`pip install -e .[dev]`, run:

```bash
pytest
```

## 7. Serialization

Every data class exposes primitives only (ints, lists, dicts), so it is straightforward to
emit JSON:

```python
import json
payload = json.dumps(result.final_state.to_dict())
```

## 8. Extending

- Integrate directly into Python services (FastAPI, Django, etc.) by importing the API.  
- For performance-sensitive workloads, you can experiment with Numba or Cython on top of
  the NumPy-backed arrays without changing the public API.  
- Keep this package side by side with the Rust/PyO3 engine to compare correctness and
  potentially use it for tests, simulations, or fallbacks.
