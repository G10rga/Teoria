"""SQLite access layer."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("PRAVA_DB", BASE_DIR / "prava.db"))
SCHEMA_PATH = BASE_DIR / "schema.sql"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# columns added after the first release; applied to existing databases on start
MIGRATIONS = {
    "tickets": {"layout": "TEXT"},
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate(conn)


def get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def upsert_ticket(conn: sqlite3.Connection, ticket: dict) -> None:
    conn.execute(
        """
        INSERT INTO tickets (id, category, question, answers_json, correct_index,
                             image, layout, explanation, source_url, imported_at)
        VALUES (:id, :category, :question, :answers_json, :correct_index,
                :image, :layout, :explanation, :source_url, :imported_at)
        ON CONFLICT(id) DO UPDATE SET
            category      = excluded.category,
            question      = excluded.question,
            answers_json  = excluded.answers_json,
            correct_index = COALESCE(excluded.correct_index, tickets.correct_index),
            image         = COALESCE(excluded.image, tickets.image),
            layout        = COALESCE(excluded.layout, tickets.layout),
            explanation   = COALESCE(excluded.explanation, tickets.explanation),
            source_url    = excluded.source_url,
            imported_at   = excluded.imported_at
        """,
        {
            "id": ticket["id"],
            "category": ticket.get("category", "B"),
            "question": ticket["question"],
            "answers_json": json.dumps(ticket["answers"], ensure_ascii=False),
            "correct_index": ticket.get("correct_index"),
            "image": ticket.get("image"),
            "layout": ticket.get("layout"),
            "explanation": ticket.get("explanation"),
            "source_url": ticket.get("source_url"),
            "imported_at": now(),
        },
    )
    conn.execute(
        "INSERT OR IGNORE INTO ticket_stats(ticket_id) VALUES (?)", (ticket["id"],)
    )


def ticket_row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["answers"] = json.loads(data.pop("answers_json"))
    return data


def load_tickets(conn: sqlite3.Connection, ids: list[int] | None = None) -> list[dict]:
    if ids is None:
        rows = conn.execute("SELECT * FROM tickets ORDER BY id").fetchall()
        return [ticket_row_to_dict(r) for r in rows]
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM tickets WHERE id IN ({placeholders})", ids
    ).fetchall()
    by_id = {r["id"]: ticket_row_to_dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]
