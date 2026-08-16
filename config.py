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


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.environ.get("TEORIA_SECRET") or "dev-only-key"
    SQLALCHEMY_DATABASE_URI = database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    if SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"check_same_thread": False}}

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("TEORIA_SECURE_COOKIES", "").lower() in ("1", "true", "yes")
    REMEMBER_COOKIE_HTTPONLY = True
    WTF_CSRF_TIME_LIMIT = 60 * 60 * 8
    WTF_CSRF_ENABLED = os.environ.get("TEORIA_CSRF", "1") != "0"
