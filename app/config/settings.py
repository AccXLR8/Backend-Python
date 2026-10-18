from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyHttpUrl, Field
from typing import List, Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development")
    debug: bool = Field(default=True)
    secret_key: str = Field(default="change-me")
    log_level: str = Field(default="INFO")

    database_url: str = Field(..., alias="DATABASE_URL")
    redis_url: str = Field(..., alias="REDIS_URL")
    azure_storage_connection_string: str = Field(..., alias="AZURE_STORAGE_CONNECTION_STRING")
    azure_blob_container: str = Field(default="replays")

    engine_module: str = Field(default="chainreaction_py")

    board_width: int = Field(default=8)
    board_height: int = Field(default=8)
    mini_board_width: int = Field(default=5)
    mini_board_height: int = Field(default=5)
    game_clock_seconds: int = Field(default=300)
    mini_game_clock_seconds: int = Field(default=600)

    websocket_heartbeat_seconds: int = Field(default=15)
    rate_limit_moves_per_minute: int = Field(default=60)

    allowed_origins: List[AnyHttpUrl] = Field(default_factory=list)

    @property
    def game_clock_ms(self) -> int:
        return self.game_clock_seconds * 1000

    @property
    def mini_game_clock_ms(self) -> int:
        return self.mini_game_clock_seconds * 1000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[arg-type]
