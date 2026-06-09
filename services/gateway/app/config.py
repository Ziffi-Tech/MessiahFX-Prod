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
    RISK_URL:          str = "http://mezna-risk:8003"
    EXECUTOR_URL:      str = "http://mezna-executor:8004"
    JOURNAL_URL:       str = "http://mezna-journal:8006"
    NOTIFICATIONS_URL: str = "http://mezna-notifications:8007"
    STRATEGY_URL:      str = "http://mezna-strategy:8002"
    BACKTEST_URL:      str = "http://mezna-backtest:8008"
    AI_FILTER_URL:     str = "http://mezna-ai-filter:8005"
    MARKET_DATA_URL:   str = "http://mezna-market-data:8001"

    # ── Session auth (defense in depth) ───────────────────────────────────────
    # Shared with the dashboard. When set, the gateway VERIFIES the operator's
    # signed token (forwarded as X-Mezna-Token) instead of trusting the
    # X-Mezna-User/Role headers, and enforces revocation. Must match the
    # dashboard's SESSION_SECRET.
    SESSION_SECRET: str = ""
    # When true, control-plane writes REQUIRE a verified token (reject header-only
    # callers). Default false for backward compat; turn on in production.
    GATEWAY_REQUIRE_AUTH: bool = False

    # ── Credential encryption ─────────────────────────────────────────────────
    # REQUIRED. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Store in .env and Coolify secrets. Losing this key = losing all stored credentials.
    CREDENTIAL_ENCRYPTION_KEY: str = ""

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Restrict in production — dashboard origin only.
    # localhost:3000/3001 are for Next.js dev server; 8501 for Streamlit (legacy).
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:8501",
        "http://dashboard:8501",
        "http://dashboard-next:3000",
    ]

    @property
    def credentials_enabled(self) -> bool:
        return bool(self.CREDENTIAL_ENCRYPTION_KEY)


settings = Settings()
