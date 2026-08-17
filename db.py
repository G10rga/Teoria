"""SQLAlchemy session, schema bootstrap, and ticket upserts."""
from __future__ import annotations

import json
from contextlib import contextmanager

from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine

db = SQLAlchemy()
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "გასაგრძელებლად შედით ანგარიშზე."
login_manager.login_message_category = "error"

_cli_app = None


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    try:
        import sqlite3
        if not isinstance(dbapi_connection, sqlite3.Connection):
            return
    except Exception:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.close()


def init_extensions(app) -> None:
    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str):
        from models import User
        if not user_id:
            return None
        return db.session.get(User, int(user_id))


def init_schema() -> None:
    """Create missing tables and upgrade a pre-user SQLite file in place."""
    import models  # noqa: F401 — register models with metadata
    db.create_all()
    _migrate_legacy_sqlite()
    _ensure_ticket_layout_column()
    _ensure_user_admin_column()


def init_db() -> None:
    """CLI entry: push an app context, then ensure the schema exists."""
    app = get_cli_app()
    with app.app_context():
        init_schema()


def get_cli_app():
    global _cli_app
    if _cli_app is None:
        from app import app as flask_app
        _cli_app = flask_app
    return _cli_app


@contextmanager
def connect():
    """Yield the SQLAlchemy session inside an application context (CLI scripts)."""
    app = get_cli_app()
    with app.app_context():
        try:
            yield db.session
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise


def now():
    from models import utcnow
    return utcnow()


def get_meta(key: str, default=None):
    from models import Meta
    row = db.session.get(Meta, key)
    return row.value if row else default


def set_meta(key: str, value) -> None:
    from models import Meta
    row = db.session.get(Meta, key)
    if row is None:
        db.session.add(Meta(key=key, value=str(value)))
    else:
        row.value = str(value)


def upsert_ticket(ticket: dict, session=None) -> None:
    from models import Ticket, utcnow
    session = session or db.session
    answers = ticket["answers"]
    answers_json = answers if isinstance(answers, str) else json.dumps(answers, ensure_ascii=False)
    imported = ticket.get("imported_at") or utcnow()
    existing = session.get(Ticket, ticket["id"])
    if existing is None:
        session.add(Ticket(
            id=ticket["id"],
            category=ticket.get("category", "B"),
            question=ticket["question"],
            answers_json=answers_json,
            correct_index=ticket.get("correct_index"),
            image=ticket.get("image"),
            layout=ticket.get("layout"),
            explanation=ticket.get("explanation"),
            source_url=ticket.get("source_url"),
            imported_at=imported,
        ))
        return
    existing.category = ticket.get("category", existing.category)
    existing.question = ticket["question"]
    existing.answers_json = answers_json
    if ticket.get("correct_index") is not None:
        existing.correct_index = ticket["correct_index"]
    if ticket.get("image"):
        existing.image = ticket["image"]
    if ticket.get("layout"):
        existing.layout = ticket["layout"]
    if ticket.get("explanation"):
        existing.explanation = ticket["explanation"]
    existing.source_url = ticket.get("source_url", existing.source_url)
    existing.imported_at = imported


def ticket_row_to_dict(row) -> dict:
    if hasattr(row, "to_dict"):
        return row.to_dict()
    data = dict(row)
    data["answers"] = json.loads(data.pop("answers_json"))
    return data


def load_tickets(ids: list[int] | None = None) -> list[dict]:
    from models import Ticket
    q = db.session.query(Ticket).order_by(Ticket.id)
    if ids is None:
        return [t.to_dict() for t in q.all()]
    if not ids:
        return []
    rows = db.session.query(Ticket).filter(Ticket.id.in_(ids)).all()
    by_id = {t.id: t.to_dict() for t in rows}
    return [by_id[i] for i in ids if i in by_id]


def claim_legacy_data(user_id: int) -> None:
    """Attach leftover single-user rows to the first registered account."""
    if get_meta("legacy_needs_owner") != "1":
        return
    from models import Attempt, Exam, TicketStat
    db.session.query(Exam).filter(Exam.user_id.is_(None)).update(
        {Exam.user_id: user_id}, synchronize_session=False
    )
    db.session.query(Attempt).filter(Attempt.user_id.is_(None)).update(
        {Attempt.user_id: user_id}, synchronize_session=False
    )
    db.session.execute(
        text("UPDATE ticket_stats SET user_id = :uid WHERE user_id IS NULL"),
        {"uid": user_id},
    )
    set_meta("legacy_needs_owner", "0")
    db.session.commit()


def _table_columns(name: str) -> set[str]:
    inspector = inspect(db.engine)
    if name not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(name)}


def _ensure_ticket_layout_column() -> None:
    cols = _table_columns("tickets")
    if cols and "layout" not in cols:
        db.session.execute(text("ALTER TABLE tickets ADD COLUMN layout VARCHAR(255)"))
        db.session.commit()


def _ensure_user_admin_column() -> None:
    cols = _table_columns("users")
    if cols and "is_admin" not in cols:
        db.session.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE"))
        db.session.execute(text("UPDATE users SET is_admin = FALSE WHERE is_admin IS NULL"))
        db.session.commit()


def _migrate_legacy_sqlite() -> None:
    """Upgrade a pre-auth local DB: add user_id, keep the ticket bank."""
    if db.engine.dialect.name != "sqlite":
        return
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    if "exams" not in tables:
        return
    exam_cols = {c["name"] for c in inspector.get_columns("exams")}
    if "user_id" in exam_cols:
        return

    with db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE exams ADD COLUMN user_id INTEGER REFERENCES users(id)"))
        conn.execute(text("ALTER TABLE attempts ADD COLUMN user_id INTEGER REFERENCES users(id)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ticket_stats_v2 (
                user_id INTEGER REFERENCES users(id),
                ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                seen INTEGER NOT NULL DEFAULT 0,
                wrong INTEGER NOT NULL DEFAULT 0,
                right_count INTEGER NOT NULL DEFAULT 0,
                correct_streak INTEGER NOT NULL DEFAULT 0,
                mastered INTEGER NOT NULL DEFAULT 0,
                last_seen DATETIME,
                PRIMARY KEY (user_id, ticket_id)
            )
        """))
        stats_cols = {c["name"] for c in inspector.get_columns("ticket_stats")} if "ticket_stats" in tables else set()
        if stats_cols and "user_id" not in stats_cols:
            conn.execute(text("""
                INSERT INTO ticket_stats_v2
                    (user_id, ticket_id, seen, wrong, right_count, correct_streak, mastered, last_seen)
                SELECT NULL, ticket_id, seen, wrong, right_count, correct_streak, mastered, last_seen
                FROM ticket_stats
            """))
            conn.execute(text("DROP TABLE ticket_stats"))
            conn.execute(text("ALTER TABLE ticket_stats_v2 RENAME TO ticket_stats"))
        conn.execute(text(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('legacy_needs_owner', '1')"
        ))
