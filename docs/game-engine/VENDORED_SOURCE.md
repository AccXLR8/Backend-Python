# Vendored engine source

The `game_engine/` package (renamed locally for clarity) and `tests/engine/` safeguard tests were imported from:

- Repository: `https://github.com/AccXLR8/Python-Engine`
- Commit: `344ca5e` (`Infinite loop problem`)

The vendored copy in this backend is now the runtime source of truth. Future engine changes should be applied to `game_engine/` and validated together with the backend test suite.
