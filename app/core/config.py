from pydantic_settings import BaseSettings
from typing import List
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Argentum AI"
    APP_ENV: str = "development"
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    ALGORITHM: str = "HS256"

    # Database
    # DATABASE_URL is auto-injected by Render as postgresql:// (sync format).
    # ASYNC_DATABASE_URL is set manually as postgresql+asyncpg:// for SQLAlchemy async engine.
    # Locally both can be set in .env as before.
    DATABASE_URL: str = ""        # injected by Render (postgresql://...) or set in .env
    ASYNC_DATABASE_URL: str = ""  # must be postgresql+asyncpg://...
    SYNC_DATABASE_URL: str = ""   # used by Alembic (postgresql://...)

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # OpenAI
    OPENAI_API_KEY: str
    OPENAI_QUIZ_MODEL: str = "gpt-4o-mini"
    OPENAI_ADVANCED_MODEL: str = "gpt-4o"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # Firebase
    FIREBASE_PROJECT_ID: str = ""
    FIREBASE_CREDENTIALS_PATH: str = "firebase-service-account.json"

    # AWS S3 — leave blank to use local storage instead
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: str = "argentum-uploads"

    # Local file storage
    # In Docker this maps to the persistent /app/data volume.
    # Locally (dev) it falls back to ./uploads next to the project root.
    LOCAL_STORAGE_DIR: str = "/app/data/uploads"

    # ChromaDB — also lives on the persistent /app/data volume in Docker.
    CHROMA_PERSIST_DIR: str = "/app/data/chroma_store"

    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 60
    UPLOAD_SIZE_LIMIT_MB: int = 25

    # CORS
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    @property
    def async_db_url(self) -> str:
        """
        Returns the correct async database URL for SQLAlchemy.
        Priority:
          1. ASYNC_DATABASE_URL if explicitly set
          2. DATABASE_URL converted to asyncpg format (Render auto-inject)
          3. SYNC_DATABASE_URL converted to asyncpg format (local .env fallback)
        """
        if self.ASYNC_DATABASE_URL:
            return self.ASYNC_DATABASE_URL
        base = self.DATABASE_URL or self.SYNC_DATABASE_URL
        return (
            base
            .replace("postgres://", "postgresql+asyncpg://", 1)
            .replace("postgresql://", "postgresql+asyncpg://", 1)
        )

    @property
    def sync_db_url(self) -> str:
        """
        Returns the correct sync database URL for Alembic / psycopg2.
        Priority:
          1. SYNC_DATABASE_URL if explicitly set
          2. DATABASE_URL (Render auto-inject is already sync format)
        """
        if self.SYNC_DATABASE_URL:
            return self.SYNC_DATABASE_URL
        return (
            self.DATABASE_URL
            .replace("postgresql+asyncpg://", "postgresql://", 1)
            .replace("postgres://", "postgresql://", 1)
        )

    @property
    def allowed_origins_list(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def use_local_storage(self) -> bool:
        """Fall back to local-disk storage when S3 creds are missing or placeholders."""
        key = self.AWS_ACCESS_KEY_ID.strip()
        return not key or key.startswith("your-")

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
