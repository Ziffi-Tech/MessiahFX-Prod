"""Notifications service configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SERVICE_NAME: str = "notifications"
    VERSION: str = "0.1.0"
    SERVICE_PORT: int = 8007
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    REDIS_URL: str = "redis://redis:6379/0"

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_ENABLED: bool = False

    # Discord
    DISCORD_WEBHOOK_URL: str = ""
    DISCORD_ENABLED: bool = False

    # Rate limiting — minimum seconds between alerts per channel
    ALERT_MIN_INTERVAL_SECONDS: float = 1.0

    # Capped backlog: if the service is down, only keep this many queued alerts
    NOTIFICATION_QUEUE_MAX_LEN: int = 500

    @property
    def any_channel_configured(self) -> bool:
        return (
            self.TELEGRAM_ENABLED and bool(self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_CHAT_ID)
        ) or (
            self.DISCORD_ENABLED and bool(self.DISCORD_WEBHOOK_URL)
        )


settings = Settings()
