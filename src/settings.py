import sys
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import ValidationError

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- core ----
    OPENROUTER_API_KEY: str
    OPENROUTER_MODEL: str = "qwen/qwen3.7-flash"
    LLM_TIMEOUT: float = 30.0
    LLM_MAX_RETRIES: int = 3
    LOG_LEVEL: str = "INFO"

    # ---- database ----
    database_url: Optional[str] = None
    db_pool_size: int = 16
    db_max_overflow: int = 8

    # ---- registry ----
    registry_ttl_s: int = 30
    config_sync_mode: str = "safe"

    # ---- feature flags ----
    saarthi_user_provider: str = "static_token"
    mitra_enabled: int = 0
    saarthi_admin_enabled: int = 0

    # ---- auth / jwt ----
    saarthi_jwt_secret: Optional[str] = None
    saarthi_jwt_verify_exp: bool = True
    saarthi_static_token: Optional[str] = None
    jwt_identifier_field: str = "id"
    jwt_email_suffix: str = "@shikshalokam.org"

try:
    settings = Settings()
except ValidationError as e:
    print(f"Configuration validation error:\n{e}")
    sys.exit(1)
