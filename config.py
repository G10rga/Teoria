"""Runtime configuration. Defaults are local SQLite; production uses env vars."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _default_sqlite_path() -> Path:
    teoria = BASE_DIR / "teoria.db"
    leftover = BASE_DIR / "prava.db"
    if teoria.exists() or not leftover.exists():
        return teoria
    return leftover


def database_uri() -> str:
    raw = os.environ.get("DATABASE_URL") or os.environ.get("TEORIA_DB")
    if not raw:
        path = _default_sqlite_path().resolve().as_posix()
        return "sqlite:///" + path
    if "://" not in raw:
        return "sqlite:///" + Path(raw).expanduser().resolve().as_posix()
    # Heroku-style URLs are postgres://; SQLAlchemy 2 needs a driver.
    if raw.startswith("postgres://"):
        raw = "postgresql+psycopg://" + raw[len("postgres://"):]
    elif raw.startswith("postgresql://") and "+psycopg" not in raw:
        raw = "postgresql+psycopg://" + raw[len("postgresql://"):]
    return raw


def _truthy(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.environ.get("TEORIA_SECRET") or "dev-only-key"
    SQLALCHEMY_DATABASE_URI = database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = (
        {"connect_args": {"check_same_thread": False}}
        if SQLALCHEMY_DATABASE_URI.startswith("sqlite")
        else {"pool_pre_ping": True, "pool_recycle": 300}
    )

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _truthy("TEORIA_SECURE_COOKIES")
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    WTF_CSRF_TIME_LIMIT = 60 * 60 * 8
    WTF_CSRF_ENABLED = os.environ.get("TEORIA_CSRF", "1") != "0"
    PREFERRED_URL_SCHEME = os.environ.get("PREFERRED_URL_SCHEME") or (
        "https" if SESSION_COOKIE_SECURE else "http"
    )
    ADMIN_EMAILS = frozenset(
        email.strip().lower()
        for email in os.environ.get("TEORIA_ADMINS", "").split(",")
        if email.strip()
    )
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
