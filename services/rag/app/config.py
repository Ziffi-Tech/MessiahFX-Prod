"""RAG service configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SERVICE_NAME: str = "rag"
    VERSION: str = "0.1.0"
    SERVICE_PORT: int = 8009
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # Qdrant
    QDRANT_URL: str = "http://qdrant:6333"
    QDRANT_COLLECTION: str = "mezna_knowledge"
    QDRANT_VECTOR_SIZE: int = 1536    # OpenAI text-embedding-3-small / ada-002

    # Redis — strategy profile storage
    REDIS_URL: str = "redis://redis:6379/0"

    # Anthropic (synthesis + analysis)
    ANTHROPIC_API_KEY: str = ""

    # Answer synthesis — Claude Haiku (cheap, fast, per-query)
    SYNTHESIS_MODEL: str = "claude-haiku-4-5"
    SYNTHESIS_MAX_TOKENS: int = 1024
    SYNTHESIS_TIMEOUT_SECONDS: float = 15.0

    # Book analysis — Claude Sonnet (deep extraction, once per document)
    ANALYSIS_MODEL: str = "claude-sonnet-4-5"
    ANALYSIS_MAX_TOKENS: int = 4096
    ANALYSIS_TIMEOUT_SECONDS: float = 180.0   # 3 min — books are long
    # Max characters sent to Claude per analysis section.
    # Large books are split into sections of this size, then synthesised.
    ANALYSIS_SECTION_CHARS: int = 12_000

    # OpenAI embeddings (cheaper + faster than Anthropic for dense vectors)
    OPENAI_API_KEY: str = ""
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_BATCH_SIZE: int = 100

    # Retrieval
    TOP_K: int = 5                    # Number of chunks returned per query
    MIN_SCORE: float = 0.65           # Minimum cosine similarity threshold
    CHUNK_SIZE: int = 800             # Characters per document chunk
    CHUNK_OVERLAP: int = 100          # Overlap between adjacent chunks

    @property
    def anthropic_configured(self) -> bool:
        return bool(self.ANTHROPIC_API_KEY)

    @property
    def openai_configured(self) -> bool:
        return bool(self.OPENAI_API_KEY)


settings = Settings()
