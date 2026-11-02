from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyHttpUrl, Field, model_validator
from typing import List, Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development")
    debug: bool = Field(default=True)
    secret_key: str = Field(default="change-me")
    log_level: str = Field(default="INFO")
    google_client_id: Optional[str] = Field(default=None, alias="GOOGLE_CLIENT_ID")

    database_url: str = Field(..., alias="DATABASE_URL")
    redis_url: str = Field(..., alias="REDIS_URL")
    azure_storage_connection_string: str = Field(..., alias="AZURE_STORAGE_CONNECTION_STRING")
    azure_blob_container: str = Field(default="replays")
    orb_skin_asset_base_url: AnyHttpUrl = Field(
        default="https://chain-reaction-frontend-ebon.vercel.app",
        alias="ORB_SKIN_ASSET_BASE_URL",
        validate_default=True,
    )

    board_width: int = Field(default=8, ge=3, le=16)
    board_height: int = Field(default=8, ge=3, le=16)
    mini_board_width: int = Field(default=5, ge=3, le=16)
    mini_board_height: int = Field(default=5, ge=3, le=16)
    game_clock_seconds: int = Field(default=300, ge=60, le=3600)
    mini_game_clock_seconds: int = Field(default=600, ge=60, le=3600)

    websocket_heartbeat_seconds: int = Field(default=15, ge=1, le=300)
    websocket_messages_per_minute: int = Field(default=120, ge=1, le=600)
    websocket_snapshots_per_minute: int = Field(default=20, ge=1, le=120)
    websocket_connections_per_minute: int = Field(default=30, ge=1, le=300)
    websocket_max_connections_per_user: int = Field(default=5, ge=1, le=20)
    rate_limit_moves_per_minute: int = Field(default=60, ge=1, le=600)
    auth_login_attempts_per_minute: int = Field(default=20, alias="AUTH_LOGIN_ATTEMPTS_PER_MINUTE", ge=1, le=120)
    auth_registration_attempts_per_minute: int = Field(default=8, alias="AUTH_REGISTRATION_ATTEMPTS_PER_MINUTE", ge=1, le=60)
    auth_guest_attempts_per_minute: int = Field(default=10, alias="AUTH_GUEST_ATTEMPTS_PER_MINUTE", ge=1, le=60)
    auth_password_changes_per_minute: int = Field(default=5, alias="AUTH_PASSWORD_CHANGES_PER_MINUTE", ge=1, le=30)
    matchmaking_queue_lease_seconds: int = Field(default=180, ge=30, le=3600)

    allowed_origins: List[AnyHttpUrl] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_production_secret(self) -> "Settings":
        if self.app_env.strip().lower() in {"production", "prod"}:
            secret = self.secret_key.strip()
            weak_values = {
                "", "change-me", "changeme", "secret", "development", "dev",
                "your-secret-key", "replace-me", "please-change-me",
            }
            if secret.lower() in weak_values or len(secret) < 32 or len(set(secret)) < 12:
                raise ValueError(
                    "SECRET_KEY must be set to a strong, randomly generated value "
                    "of at least 32 characters in production"
                )
            if self.orb_skin_asset_base_url.scheme != "https":
                raise ValueError("ORB_SKIN_ASSET_BASE_URL must use HTTPS in production")
        return self

    @property
    def game_clock_ms(self) -> int:
        return self.game_clock_seconds * 1000

    @property
    def mini_game_clock_ms(self) -> int:
        return self.mini_game_clock_seconds * 1000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[arg-type]
