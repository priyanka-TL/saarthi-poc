import sys
from typing import Literal, Optional
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
    config_sync_mode: Literal["safe", "force", "off"] = "safe"

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

    # ---- mitra REST ----
    # MITRA_ORIGIN_URL is a credential: Mitra gates admission on the Origin
    # header. Never log it, never include it in error responses, never put
    # it in YAML. See design doc §13.2 and the comment in MitraRestClient.
    mitra_base_url: str = "https://mitra.example.com"
    mitra_origin_url: str = "https://mitra.example.com"
    mitra_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    # Comma-separated EXTRA hostnames allowed in returned URLs (the
    # mitra_base_url hostname is always included automatically).
    mitra_allowed_hosts: str = ""
    mitra_connect_timeout_s: float = 10.0
    mitra_read_timeout_s: float = 30.0

    # ---- mitra WebSocket (MitraChannel) ----
    mitra_ws_url: str = "wss://mitra.example.com/ws/common/"
    mitra_ws_connect_timeout_s: float = 10.0
    # Address fields sent in the authenticate frame. Mitra doesn't validate
    # these against anything real for the guest flows this integration uses.
    mitra_ip_city: str = ""
    mitra_ip_state: str = ""
    mitra_ip_zip: str = ""

    # ---- mitra channel pool (MitraSessionManager) ----
    # Each open channel is a socket plus a thread -- bound the LRU. 200 is
    # fine; 5,000 is not (design doc §7.6/§13.1).
    mitra_max_open_channels: int = 200
    mitra_idle_close_s: float = 1200.0

try:
    settings = Settings()
except ValidationError as e:
    print(f"Configuration validation error:\n{e}")
    sys.exit(1)
