"""Gateway service configuration — loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Service identity ──────────────────────────────────────────────────────
    SERVICE_NAME: str = "gateway"
    VERSION: str = "0.1.0"
    SERVICE_PORT: int = 8000
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ── Infrastructure ────────────────────────────────────────────────────────
    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379/0"

    # ── Trading mode ──────────────────────────────────────────────────────────
    TRADING_MODE: str = "paper"

    # ── Internal service URLs ─────────────────────────────────────────────────
    RISK_URL: str = "http://risk:8003"
    EXECUTOR_URL: str = "http://executor:8004"
    JOURNAL_URL: str = "http://journal:8006"
    NOTIFICATIONS_URL: str = "http://notifications:8007"

    # ── Credential encryption ─────────────────────────────────────────────────
    # REQUIRED. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Store in .env and Coolify secrets. Losing this key = losing all stored credentials.
    CREDENTIAL_ENCRYPTION_KEY: str = ""

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Restrict in production — dashboard origin only
    CORS_ORIGINS: list[str] = ["http://localhost:8501", "http://dashboard:8501"]

    @property
    def credentials_enabled(self) -> bool:
        return bool(self.CREDENTIAL_ENCRYPTION_KEY)


settings = Settings()
