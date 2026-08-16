"""Exam generation + grading logic.

Core guarantee: inside one cycle every ticket appears in exactly one exam.
921 tickets -> exams 1..30 with 30 tickets + exam 31 with 21 tickets.
No ticket can repeat across exams of the same cycle, which is exactly what
teoria.on.ge fails to do.
"""
from __future__ import annotations

import secrets
import sqlite3
from random import Random

import db

QUESTIONS_PER_EXAM = 30
MAX_ERRORS = 3          # real exam: 4th mistake = fail
TIME_LIMIT_MIN = 30     # real exam: 30 minutes


# ---------------------------------------------------------------- generation
def current_cycle(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(cycle) AS c FROM exams").fetchone()
    return int(row["c"] or 0)


def generate_cycle(conn: sqlite3.Connection, seed: str | None = None) -> int:
    """Shuffle the whole ticket bank and cut it into non-overlapping exams."""
    ticket_ids = [r["id"] for r in conn.execute("SELECT id FROM tickets ORDER BY id")]
    if not ticket_ids:
        raise RuntimeError("ბილეთები არ არის ბაზაში — ჯერ გაუშვი scraper.py")

    seed = seed or secrets.token_hex(16)
    Random(seed).shuffle(ticket_ids)          # seeded => reproducible, still unbiased

    cycle = current_cycle(conn) + 1
    chunks = [
        ticket_ids[i : i + QUESTIONS_PER_EXAM]
        for i in range(0, len(ticket_ids), QUESTIONS_PER_EXAM)
    ]
    for number, chunk in enumerate(chunks, start=1):
        cur = conn.execute(
            "INSERT INTO exams(cycle, number, kind, seed, created_at) "
            "VALUES (?, ?, 'base', ?, ?)",
            (cycle, number, seed, db.now()),
        )
        exam_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO exam_tickets(exam_id, position, ticket_id) VALUES (?, ?, ?)",
            [(exam_id, pos, tid) for pos, tid in enumerate(chunk, start=1)],
        )
    conn.commit()
    return cycle


def mistake_pool(conn: sqlite3.Connection) -> list[int]:
    """Tickets answered wrong and not yet re-learned, hardest first."""
    rows = conn.execute(
        """
        SELECT ticket_id FROM ticket_stats
        WHERE wrong > 0 AND mastered = 0
        ORDER BY wrong DESC, correct_streak ASC, last_seen ASC
        """
    ).fetchall()
    return [r["ticket_id"] for r in rows]


def unseen_pool(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        "SELECT ticket_id FROM ticket_stats WHERE seen = 0 ORDER BY RANDOM()"
    ).fetchall()
    return [r["ticket_id"] for r in rows]


def create_review_exam(conn: sqlite3.Connection, size: int = QUESTIONS_PER_EXAM) -> int | None:
    """Build an exam out of the mistakes; top up with unseen tickets if short."""
    pool = mistake_pool(conn)[:size]
    if not pool:
        return None
    if len(pool) < size:
        pool += [t for t in unseen_pool(conn) if t not in pool][: size - len(pool)]

    Random(secrets.token_hex(8)).shuffle(pool)
    cur = conn.execute(
        "INSERT INTO exams(cycle, number, kind, seed, created_at) "
        "VALUES (?, NULL, 'review', NULL, ?)",
        (max(current_cycle(conn), 1), db.now()),
    )
    exam_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO exam_tickets(exam_id, position, ticket_id) VALUES (?, ?, ?)",
        [(exam_id, pos, tid) for pos, tid in enumerate(pool, start=1)],
    )
    conn.commit()
    return exam_id


# ------------------------------------------------------------------- attempts
def exam_ticket_ids(conn: sqlite3.Connection, exam_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT ticket_id FROM exam_tickets WHERE exam_id = ? ORDER BY position",
        (exam_id,),
    ).fetchall()
    return [r["ticket_id"] for r in rows]


def start_attempt(conn: sqlite3.Connection, exam_id: int, mode: str = "exam") -> int:
    cur = conn.execute(
        "INSERT INTO attempts(exam_id, mode, started_at) VALUES (?, ?, ?)",
        (exam_id, mode, db.now()),
    )
    conn.commit()
    return cur.lastrowid


def save_answer(conn: sqlite3.Connection, attempt_id: int, ticket_id: int,
                chosen_index: int | None) -> bool:
    row = conn.execute(
        "SELECT correct_index FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    correct = row["correct_index"] if row else None
    is_correct = int(chosen_index is not None and correct is not None
                     and chosen_index == correct)
    conn.execute(
        """
        INSERT INTO attempt_answers(attempt_id, ticket_id, chosen_index, is_correct, answered_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(attempt_id, ticket_id) DO UPDATE SET
            chosen_index = excluded.chosen_index,
            is_correct   = excluded.is_correct,
            answered_at  = excluded.answered_at
        """,
        (attempt_id, ticket_id, chosen_index, is_correct, db.now()),
    )
    conn.commit()
    return bool(is_correct)


def finish_attempt(conn: sqlite3.Connection, attempt_id: int, seconds: int | None = None) -> dict:
    attempt = conn.execute(
        "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
    ).fetchone()
    if attempt is None:
        raise KeyError(attempt_id)
    ticket_ids = exam_ticket_ids(conn, attempt["exam_id"])

    # make sure unanswered questions are recorded as blanks (= wrong)
    for tid in ticket_ids:
        conn.execute(
            "INSERT OR IGNORE INTO attempt_answers(attempt_id, ticket_id, chosen_index, "
            "is_correct, answered_at) VALUES (?, ?, NULL, 0, ?)",
            (attempt_id, tid, db.now()),
        )

    rows = conn.execute(
        "SELECT ticket_id, is_correct FROM attempt_answers WHERE attempt_id = ?",
        (attempt_id,),
    ).fetchall()
    correct_count = sum(1 for r in rows if r["is_correct"])
    wrong_count = len(rows) - correct_count
    passed = int(wrong_count <= MAX_ERRORS)

    if attempt["finished_at"] is None:          # stats only count the first grading
        for r in rows:
            _update_ticket_stats(conn, r["ticket_id"], bool(r["is_correct"]))

    conn.execute(
        "UPDATE attempts SET finished_at = ?, correct_count = ?, wrong_count = ?, "
        "passed = ?, seconds_spent = ? WHERE id = ?",
        (db.now(), correct_count, wrong_count, passed, seconds, attempt_id),
    )
    conn.commit()
    return {
        "attempt_id": attempt_id,
        "correct": correct_count,
        "wrong": wrong_count,
        "total": len(rows),
        "passed": bool(passed),
    }


def _update_ticket_stats(conn: sqlite3.Connection, ticket_id: int, correct: bool) -> None:
    conn.execute("INSERT OR IGNORE INTO ticket_stats(ticket_id) VALUES (?)", (ticket_id,))
    row = conn.execute(
        "SELECT * FROM ticket_stats WHERE ticket_id = ?", (ticket_id,)
    ).fetchone()
    seen = row["seen"] + 1
    wrong = row["wrong"] + (0 if correct else 1)
    right_count = row["right_count"] + (1 if correct else 0)
    streak = row["correct_streak"] + 1 if correct else 0
    # never missed -> one correct answer is enough; missed before -> needs two in a row
    needed = 2 if wrong > 0 else 1
    mastered = int(streak >= needed)
    conn.execute(
        "UPDATE ticket_stats SET seen = ?, wrong = ?, right_count = ?, correct_streak = ?, "
        "mastered = ?, last_seen = ? WHERE ticket_id = ?",
        (seen, wrong, right_count, streak, mastered, db.now(), ticket_id),
    )


# ------------------------------------------------------------------ reporting
def overview(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"]
    stats = conn.execute(
        """
        SELECT
            COALESCE(SUM(seen > 0), 0)                      AS seen,
            COALESCE(SUM(mastered = 1), 0)                   AS mastered,
            COALESCE(SUM(wrong > 0 AND mastered = 0), 0)     AS pending
        FROM ticket_stats
        """
    ).fetchone()
    attempts = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(passed), 0) p FROM attempts WHERE finished_at IS NOT NULL"
    ).fetchone()
    missing_key = conn.execute(
        "SELECT COUNT(*) c FROM tickets WHERE correct_index IS NULL"
    ).fetchone()["c"]
    return {
        "total": total,
        "seen": stats["seen"],
        "mastered": stats["mastered"],
        "pending": stats["pending"],
        "unseen": max(total - stats["seen"], 0),
        "attempts": attempts["c"],
        "passed_attempts": attempts["p"],
        "missing_key": missing_key,
        "coverage_pct": round(100 * stats["seen"] / total) if total else 0,
        "mastered_pct": round(100 * stats["mastered"] / total) if total else 0,
    }


def exam_cards(conn: sqlite3.Connection, cycle: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT e.id, e.number, e.kind,
               (SELECT COUNT(*) FROM exam_tickets et WHERE et.exam_id = e.id) AS size,
               (SELECT COUNT(*) FROM attempts a WHERE a.exam_id = e.id AND a.finished_at IS NOT NULL) AS tries,
               (SELECT MAX(a.correct_count) FROM attempts a WHERE a.exam_id = e.id AND a.finished_at IS NOT NULL) AS best,
               (SELECT MAX(a.passed) FROM attempts a WHERE a.exam_id = e.id AND a.finished_at IS NOT NULL) AS ever_passed,
               (SELECT a.id FROM attempts a WHERE a.exam_id = e.id ORDER BY a.id DESC LIMIT 1) AS last_attempt
        FROM exams e
        WHERE e.cycle = ? AND e.kind = 'base'
        ORDER BY e.number
        """,
        (cycle,),
    ).fetchall()
    cards = []
    for r in rows:
        status = "new"
        if r["tries"]:
            status = "passed" if r["ever_passed"] else "failed"
        cards.append({**dict(r), "status": status})
    return cards


def review_exams(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """
        SELECT e.id, e.created_at,
               (SELECT COUNT(*) FROM exam_tickets et WHERE et.exam_id = e.id) AS size,
               (SELECT MAX(a.correct_count) FROM attempts a WHERE a.exam_id = e.id AND a.finished_at IS NOT NULL) AS best,
               (SELECT MAX(a.passed) FROM attempts a WHERE a.exam_id = e.id AND a.finished_at IS NOT NULL) AS ever_passed,
               (SELECT COUNT(*) FROM attempts a WHERE a.exam_id = e.id AND a.finished_at IS NOT NULL) AS tries
        FROM exams e WHERE e.kind = 'review'
        ORDER BY e.id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
