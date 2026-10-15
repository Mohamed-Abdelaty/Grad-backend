"""Application settings (environment-driven; never commit real secrets)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All backend configuration comes from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite:///./chaosflow.db"
    JWT_SECRET_KEY: str = "dev-only-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"
    APP_VERSION: str = "0.1.0"
    LOG_LEVEL: str = "INFO"
    ENGINE_BATCH_SIZE: int = 500
    ENGINE_MAX_BACKOFF_SECONDS: float = 60.0
    RUN_WORKER_THREADS: int = 4
    ADMIN_EMAIL: str = ""
    ADMIN_PASSWORD: str = ""

    @property
    def cors_origins(self) -> list[str]:
        """Parse the comma-separated CORS origins list."""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_postgres(self) -> bool:
        """Whether the configured database is PostgreSQL."""
        return self.DATABASE_URL.startswith("postgresql")


settings = Settings()
