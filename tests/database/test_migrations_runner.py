from __future__ import annotations

from types import SimpleNamespace

from app.database import migrations_runner


class FakeAlembicConfig:
    last_instance = None

    def __init__(self, path: str):
        self.path = path
        self.main_options: dict[str, str] = {}
        self.attributes: dict[str, object] = {}
        FakeAlembicConfig.last_instance = self

    def set_main_option(self, key: str, value: str) -> None:
        self.main_options[key] = value


def test_embedded_migrations_preserve_logging_and_use_normalized_url(monkeypatch):
    database_url = (
        "postgresql+asyncpg://user:password@example.neon.tech/db"
        "?ssl=require&channel_binding=require"
    )
    calls: list[tuple[FakeAlembicConfig, str]] = []
    monkeypatch.setattr(
        migrations_runner,
        "get_settings",
        lambda: SimpleNamespace(database_url=database_url),
    )
    monkeypatch.setattr(migrations_runner, "Config", FakeAlembicConfig)
    monkeypatch.setattr(
        migrations_runner.command,
        "upgrade",
        lambda config, revision: calls.append((config, revision)),
    )
    monkeypatch.setattr(migrations_runner, "_MIGRATIONS_RAN", False)

    migrations_runner.run_migrations_once()

    config = FakeAlembicConfig.last_instance
    assert config is not None
    assert config.attributes["configure_logger"] is False
    assert config.attributes["database_url"] == config.main_options["sqlalchemy.url"]
    assert config.main_options["sqlalchemy.url"].startswith("postgresql://")
    assert "sslmode=require" in config.main_options["sqlalchemy.url"]
    assert "channel_binding" not in config.main_options["sqlalchemy.url"]
    assert calls == [(config, "head")]
