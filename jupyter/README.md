# Chain Reaction self-play trainer

This folder contains a self-contained Google Colab notebook for training a Chain Reaction policy/value model through self-play: [chain_reaction_self_play.ipynb](chain_reaction_self_play.ipynb).

The notebook includes its own game simulator, neural network, Monte Carlo Tree Search (MCTS), self-play data generation, optimizer loop, checkpoint save/resume support, and basic evaluation helpers. It does not import the backend's `app`, `game_engine`, or database code. Its main dependencies are NumPy and PyTorch, plus Google Colab's Drive integration when saving to Drive.

## Current training setup

The notebook configuration currently uses:

| Setting | Value | What it controls |
| --- | ---: | --- |
| Board | 8×8 | Each move selects one of 64 cells. |
| Maximum game length | 500 plies | A ply is one player's move. Capped games are scored by board material. |
| MCTS simulations | 32 per move | Search effort used to choose self-play moves. |
| Self-play games | 4 per iteration | New games added to the replay buffer each iteration. |
| Training steps | 40 per iteration | Minibatch optimizer updates after self-play. |
| Batch size | 64 | Replay examples per optimizer update. |
| Replay capacity | 20,000 positions | Older examples are discarded when the buffer fills. |
| Target iteration | 5,000 | Total iteration number to reach, including any resumed iterations. |
| Latest checkpoint | Every iteration | Supports resuming from the most recently completed iteration. |
| Numbered snapshot interval | 50 iterations | Keeps named archive copies at iterations 50, 100, 150, and so on. |

These are configuration choices, not guarantees about training quality or runtime. The notebook selects a CUDA GPU when PyTorch reports one; otherwise it uses the CPU. It does not currently configure a TPU.

## Game rules implemented

The simulator represents the board with an owner and an orb count for every cell. An owner of `-1` means the cell is empty. Player 0 moves first.

- A move adds one orb to an empty cell or a cell owned by the player moving. An opponent-owned cell is not a legal target.
- A cell's critical mass is its number of orthogonal neighbors: two in a corner, three on an edge, and four in the interior.
- When a cell reaches critical mass, it explodes. The cell is cleared, and one orb is placed on each orthogonal neighbor. Each neighbor becomes owned by the exploding player. Any excess orbs in the exploding cell are consumed.
- Explosions resolve in waves. After each wave, a player who has moved and owns no cells is eliminated. The game ends when only one player remains.
- The simulator imposes a 500-ply cap because games can run for a long time. If no winner exists at the cap, it compares material: owned-cell count plus 0.15 times the orbs on those cells. A higher score wins; a tie is a draw.

The explosion behavior is specific to this project's rules: an explosion distributes one orb to each neighbor, regardless of the exploding cell's orb count.

## How a position becomes input to the model

The network sees five board-shaped input planes, always oriented from the side-to-move's perspective:

1. Cells owned by the player to move.
2. Cells owned by the opponent.
3. Orb counts divided by four and clipped to the range 0–2.
4. A plane indicating whether the player to move has made a move yet.
5. A plane indicating whether the opponent has made a move yet.

Using the current player's perspective lets the same network play either color. A separate legal-move mask prevents the policy from selecting opponent-owned cells or making moves after a game ends.

## Policy/value neural network

`PolicyValueNet` is a convolutional residual network with 64 feature channels and two residual blocks.

- The **policy head** returns one logit per board cell: 64 logits on the configured 8×8 board. After illegal cells are masked, the logits become move probabilities.
- The **value head** returns one number in `[-1, 1]`, estimating the eventual result from the side-to-move's perspective.

The optimizer is AdamW with learning rate `2e-4`, weight decay `1e-4`, and gradient clipping at 1.0. The notebook does not currently use a learning-rate schedule.

## MCTS move selection

At a position, MCTS first gets move priors and a value estimate from the network. It then runs the configured number of simulations through the simulator. At each node, it balances:

- the estimated outcome of a move (the average value of visits), and
- the network's prior probability for the move, adjusted to encourage exploration.

The root's visit counts form a policy target for training. During self-play, Dirichlet noise is mixed into root move priors to encourage exploration. The action is sampled from visit counts with temperature 1.0 for the first 20 plies and 0.15 afterwards. Evaluation uses the most-visited move without root noise.

## Self-play and training loop

Each training iteration performs these steps:

1. Play four games using the current network and MCTS. Both players use that same current network, so the training opponent changes as the network changes. There is no frozen older-model opponent in this loop.
2. Save every visited position, its MCTS visit-count policy, and the player to move.
3. When a game ends, label each saved position with `+1` for a win, `-1` for a loss, or `0` for a draw, always from that position's side-to-move perspective. A capped game uses the material result described above.
4. Add the positions to a replay buffer that holds at most 20,000 records.
5. If there are at least 64 records, take 40 minibatch optimizer steps. Each step minimizes policy cross-entropy plus value mean-squared error.
6. Print iteration metrics: number of games, mean plies, cap rate, replay size, mean combined loss, and elapsed seconds.

The printed loss is the average of policy and value losses added together across that iteration's optimizer steps. Self-play targets change as the network changes, so a rising or falling loss by itself does not establish whether the bot is getting stronger. The notebook does not automatically promote a “best” model based on match results.

## Checkpoints and resuming

Google Drive is mounted at `/content/drive`, and checkpoints are written to:

```text
MyDrive/chain_reaction_checkpoints/
```

The 8×8 filenames are deliberately distinct from older 6×6 checkpoints:

```text
chain_reaction_8x8_latest.pt
chain_reaction_8x8_previous.pt
chain_reaction_8x8_iter_0500.pt
chain_reaction_8x8_iter_1000.pt
...
```

After every completed iteration, the notebook writes a temporary latest file and renames it into place, rotating the prior latest checkpoint to `previous`. Every 50 iterations it also saves a numbered snapshot. A checkpoint contains model weights, optimizer state, iteration and history, replay examples, configuration, and Python, NumPy, and PyTorch RNG states. This lets a later runtime continue the same 8×8 run. The RNG restore code converts saved PyTorch states to CPU byte tensors for compatibility.

`ITERATIONS = 5000` is a **total target**, not “5,000 more iterations.” If the 8×8 checkpoint says iteration 1,000, resuming with this setting continues through iteration 5,000. On the first 8×8 run, no 8×8 checkpoint should exist, so it starts from a new model. A 6×6 checkpoint cannot be resumed as 8×8: the policy output and value-head input dimensions differ. If Drive is unavailable, the fallback checkpoint folder is `/content/chain_reaction_checkpoints`, which is temporary and disappears when the runtime is reset.

### Starting or resuming in Colab

1. Open the notebook in Colab and select **Runtime → Run all**.
2. Allow Drive access when prompted.
3. On a fresh 8×8 run, confirm the output says no checkpoint was found and it is starting fresh. On a resumed run, confirm it reports `resumed iteration N` for the expected checkpoint.
4. The training cell runs until the saved iteration reaches `ITERATIONS`. It updates the latest checkpoint after every completed iteration and writes a numbered snapshot at every multiple of 50. If a runtime stops during an iteration before its save finishes, that in-progress iteration must be repeated.
5. In Colab's Files pane, open `drive` → `MyDrive` → `chain_reaction_checkpoints` to download the numbered snapshots or latest model.

## Evaluation helpers

The notebook includes `evaluate_vs_random(games=12, simulations=64)`. It alternates which seat the bot plays, and returns per-game scores: 1 for a win, 0 for a loss, and 0.5 for a draw. `score_rate` is the mean of those values. For example, `0.95` over 20 games means 19 wins and one loss in that batch.

Run evaluation after training has stopped, using the same game count and simulation setting when comparing checkpoints. Small batches can be noisy, and a strong score against a random player does not show how the model performs against a trained opponent. To compare two trained checkpoints properly, load each model in turn, have them play one another with both seat assignments, and use enough games to reduce randomness; the current notebook does not include that frozen-checkpoint match evaluator.

Example evaluation cell:

```python
print("Current iteration:", iteration)
print(evaluate_vs_random(games=20, simulations=64))
```

`choose_bot_move(state, simulations=128)` is the inference helper for selecting one move from a state. `play_bot_vs_bot()` runs the current model against itself; it is not a comparison against an older checkpoint.

## Limitations and interpretation

- The model trains through self-play and can reinforce its current style; no opponent pool, frozen baseline, or automatic promotion gate is implemented.
- There is no fixed validation set. Track results against random and, ideally, frozen earlier checkpoints alongside training metrics.
- The reported combined loss is not a direct measure of playing strength.
- Training cost is dominated by many MCTS searches and simulator calls. GPU acceleration may help network operations, but the Python game simulation remains CPU work. The current code uses CUDA if available and does not support TPU device selection.
- Board size and rule version are checked when loading a checkpoint. Keep the same notebook rules and board size when resuming.
