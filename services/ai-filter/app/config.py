"""AI filter service configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SERVICE_NAME: str = "ai-filter"
    VERSION: str = "0.1.0"
    SERVICE_PORT: int = 8005
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379/0"

    # ── Anthropic ────────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""

    # Fast path (800ms timeout): Haiku + tool use for signal scoring
    AI_SCORING_MODEL: str = "claude-haiku-4-5"

    # Slow path (no realtime constraint): Sonnet + extended thinking for:
    #   - POST /ai/analyse        — per-trade deep analysis
    #   - POST /ai/regime         — market regime classification
    #   - POST /ai/digest         — performance narrative digest
    AI_ANALYSIS_MODEL: str = "claude-sonnet-4-5"

    # Hard timeout on the live signal scoring path.
    # Exceeded → pass-through. Risk engine acts without AI score.
    AI_TIMEOUT_MS: int = 800

    # Regime detection is expensive (extended thinking).
    # Cache result in Redis for this many seconds (default: 15 min).
    AI_REGIME_CACHE_TTL_SECONDS: int = 900

    # ── News Sentiment Cache ─────────────────────────────────────────────────
    # Background task: fetches crypto/FX headlines and scores them with Haiku.
    # Score cached in Redis (ai:sentiment:crypto, ai:sentiment:fx) for the scorer.
    NEWS_FETCH_ENABLED: bool = True
    NEWS_FETCH_INTERVAL_SECONDS: int = 300  # 5 minutes
    # Optional: CryptoPanic API key for richer crypto news (falls back to RSS if empty)
    CRYPTOPANIC_API_KEY: str = ""

    # ── Service URLs (for agent tool calls) ──────────────────────────────────
    # These match the internal Docker/Podman network names in compose files.
    JOURNAL_URL: str = "http://journal:8006"
    BACKTEST_URL: str = "http://backtest:8008"
    RAG_URL: str = "http://rag:8009"
    RISK_URL: str = "http://risk:8003"

    @property
    def ai_configured(self) -> bool:
        return bool(self.ANTHROPIC_API_KEY)


settings = Settings()
