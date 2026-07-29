"""
Central configuration. All environment-dependent values live here.
Termux/dev defaults to SQLite + in-memory event bus.
Production overrides via .env -> Postgres + Redis, with zero code changes.

Deliberately pure-Python: stdlib `dataclasses` + `os.environ` instead of
pydantic-settings. pydantic-settings pulls in pydantic v2 -> pydantic-core,
a Rust/PyO3 extension with no published Android/Termux wheel at any Python
version, forcing a from-source Rust build on every install. A short
dataclass loader gives the same "typed settings from env" ergonomics with
zero compiled dependencies. See docs/TERMUX.md.
"""
import os
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader: KEY=VALUE per line, no external dependency.
    Only sets a variable if not already present in the environment,
    matching python-dotenv's default (non-overriding) behavior."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_TRUE_VALUES = {"1", "true", "yes", "on"}


def _coerce(raw: str, target_type):
    if target_type is bool:
        return raw.lower() in _TRUE_VALUES
    if target_type is int:
        return int(raw)
    return raw


@dataclass(frozen=True)
class Settings:
    APP_NAME: str = "SYJ Autonomous Enterprise OS"
    ENV: str = "development"  # development | production
    DEBUG: bool = True

    # --- Database ---
    # Dev (Termux): sqlite+aiosqlite:///./saeos_dev.db
    # Prod: postgresql+asyncpg://user:pass@host:5432/saeos
    DATABASE_URL: str = "sqlite+aiosqlite:///./saeos_dev.db"

    # --- Auth ---
    JWT_SECRET: str = "change-me-in-.env"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # --- Multi-tenancy ---
    DEFAULT_TENANT_HEADER: str = "X-Tenant-ID"

    # --- Event bus backend: "memory" (Termux/dev) or "redis" (production) ---
    EVENT_BUS_BACKEND: str = "memory"
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- AI Gateway ---
    AI_ROUTING_CONFIG_PATH: str = "app/ai_gateway/routing.yaml"
    ANTHROPIC_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    VOYAGE_API_KEY: str = ""
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # --- Phase 4: async workflow execution (production only) ---
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    WORKFLOW_ASYNC_ENABLED: bool = False

    # --- Phase 4: vector store backend ---
    VECTOR_STORE_BACKEND: str = "memory"  # "memory" (Termux-safe default) or "pgvector" (production)
    VECTOR_DIMENSIONS: int = 768  # must match your embedding model's output size

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        kwargs = {}
        for f in fields(cls):
            if f.name in os.environ:
                kwargs[f.name] = _coerce(os.environ[f.name], f.type if isinstance(f.type, type) else str)
        return cls(**kwargs)


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
