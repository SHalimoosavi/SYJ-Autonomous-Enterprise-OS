"""
Ensures test-specific settings (isolated DB file, non-default JWT secret)
are in place before ANY app module is imported. pytest loads conftest.py
at collection time, before importing test modules, so this reliably wins
the race against app.core.config's lru_cache'd get_settings().
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_saeos.db")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")
