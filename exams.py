"""Exam generation + grading. Every exam, attempt, and mistake is scoped to a user.

Core guarantee: inside one cycle of one user every ticket appears in exactly one exam.
921 tickets -> exams 1..30 with 30 tickets + exam 31 with 21 tickets.
"""
from __future__ import annotations

import secrets
from random import Random

from sqlalchemy import and_, case, func, or_

from db import db
from models import (
    Attempt, AttemptAnswer, Exam, ExamTicket, FailedQuestion, Ticket, TicketStat, utcnow,
)

QUESTIONS_PER_EXAM = 30
MAX_ERRORS = 3
TIME_LIMIT_MIN = 30


def current_cycle(user_id: int) -> int:
    val = db.session.query(func.max(Exam.cycle)).filter(
        Exam.user_id == user_id, Exam.kind == "base",
    ).scalar()
    return int(val or 0)


def generate_cycle(user_id: int, seed: str | None = None) -> int:
    """Shuffle the whole ticket bank and cut it into non-overlapping exams for this user."""
    ticket_ids = [tid for (tid,) in db.session.query(Ticket.id).order_by(Ticket.id).all()]
    if not ticket_ids:
        raise RuntimeError("ბილეთები არ არის ბაზაში — ჯერ გაუშვი scraper.py")

    seed = seed or secrets.token_hex(16)
    Random(seed).shuffle(ticket_ids)

    cycle = current_cycle(user_id) + 1
    chunks = [
        ticket_ids[i : i + QUESTIONS_PER_EXAM]
        for i in range(0, len(ticket_ids), QUESTIONS_PER_EXAM)
    ]
    for number, chunk in enumerate(chunks, start=1):
        exam = Exam(user_id=user_id, cycle=cycle, number=number, kind="base", seed=seed)
        db.session.add(exam)
        db.session.flush()
        db.session.add_all([
            ExamTicket(exam_id=exam.id, position=pos, ticket_id=tid)
            for pos, tid in enumerate(chunk, start=1)
        ])
    db.session.commit()
    return cycle


def mistake_pool(user_id: int) -> list[int]:
    """Tickets this user answered wrong and has not yet re-learned, hardest first."""
    rows = (
        db.session.query(TicketStat.ticket_id)
        .filter(
            TicketStat.user_id == user_id,
            TicketStat.wrong > 0,
            TicketStat.mastered.is_(False),
        )
        .order_by(TicketStat.wrong.desc(), TicketStat.correct_streak.asc(), TicketStat.last_seen.asc())
        .all()
    )
    return [tid for (tid,) in rows]


def unseen_pool(user_id: int) -> list[int]:
    seen_ids = {
        tid for (tid,) in db.session.query(TicketStat.ticket_id).filter(
            TicketStat.user_id == user_id, TicketStat.seen > 0,
        )
    }
    all_ids = [tid for (tid,) in db.session.query(Ticket.id).order_by(func.random()).all()]
    return [tid for tid in all_ids if tid not in seen_ids]


def create_review_exam(user_id: int, size: int = QUESTIONS_PER_EXAM) -> int | None:
    """Build an exam out of this user's mistakes; top up with unseen tickets if short."""
    pool = mistake_pool(user_id)[:size]
    if not pool:
        return None
    if len(pool) < size:
        pool += [t for t in unseen_pool(user_id) if t not in pool][: size - len(pool)]

    Random(secrets.token_hex(8)).shuffle(pool)
    exam = Exam(user_id=user_id, cycle=max(current_cycle(user_id), 1), number=None, kind="review")
    db.session.add(exam)
    db.session.flush()
    db.session.add_all([
        ExamTicket(exam_id=exam.id, position=pos, ticket_id=tid)
        for pos, tid in enumerate(pool, start=1)
    ])
    db.session.commit()
    return exam.id


def exam_ticket_ids(exam_id: int) -> list[int]:
    rows = (
        db.session.query(ExamTicket.ticket_id)
        .filter(ExamTicket.exam_id == exam_id)
        .order_by(ExamTicket.position)
        .all()
    )
    return [tid for (tid,) in rows]


def start_attempt(user_id: int, exam_id: int, mode: str = "exam") -> int:
    attempt = Attempt(user_id=user_id, exam_id=exam_id, mode=mode)
    db.session.add(attempt)
    db.session.commit()
    return attempt.id


def save_answer(attempt_id: int, ticket_id: int, chosen_index: int | None) -> bool | None:
    attempt = db.session.get(Attempt, attempt_id)
    if attempt is None or attempt.finished_at:
        return None
    ticket = db.session.get(Ticket, ticket_id)
    correct = ticket.correct_index if ticket else None
    is_correct = chosen_index is not None and correct is not None and chosen_index == correct

    row = db.session.get(AttemptAnswer, {"attempt_id": attempt_id, "ticket_id": ticket_id})
    if row is None:
        db.session.add(AttemptAnswer(
            attempt_id=attempt_id,
            ticket_id=ticket_id,
            chosen_index=chosen_index,
            is_correct=is_correct,
        ))
    else:
        row.chosen_index = chosen_index
        row.is_correct = is_correct
        row.answered_at = utcnow()
    db.session.commit()
    return bool(is_correct)


def finish_attempt(attempt_id: int, seconds: int | None = None) -> dict:
    attempt = db.session.get(Attempt, attempt_id)
    if attempt is None:
        raise KeyError(attempt_id)
    ticket_ids = exam_ticket_ids(attempt.exam_id)
    first_grade = attempt.finished_at is None

    for tid in ticket_ids:
        existing = db.session.get(AttemptAnswer, {"attempt_id": attempt_id, "ticket_id": tid})
        if existing is None:
            db.session.add(AttemptAnswer(
                attempt_id=attempt_id, ticket_id=tid,
                chosen_index=None, is_correct=False,
            ))

    db.session.flush()
    rows = db.session.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt_id).all()
    correct_count = sum(1 for r in rows if r.is_correct)
    wrong_count = len(rows) - correct_count
    passed = wrong_count <= MAX_ERRORS

    if first_grade:
        for r in rows:
            _update_ticket_stats(attempt.user_id, r.ticket_id, bool(r.is_correct))
            if not r.is_correct:
                db.session.add(FailedQuestion(
                    user_id=attempt.user_id,
                    ticket_id=r.ticket_id,
                    attempt_id=attempt.id,
                    chosen_index=r.chosen_index,
                ))

    attempt.finished_at = utcnow()
    attempt.correct_count = correct_count
    attempt.wrong_count = wrong_count
    attempt.passed = passed
    attempt.seconds_spent = seconds
    db.session.commit()
    return {
        "attempt_id": attempt_id,
        "correct": correct_count,
        "wrong": wrong_count,
        "total": len(rows),
        "passed": bool(passed),
    }


def _update_ticket_stats(user_id: int, ticket_id: int, correct: bool) -> None:
    row = db.session.get(TicketStat, {"user_id": user_id, "ticket_id": ticket_id})
    if row is None:
        row = TicketStat(user_id=user_id, ticket_id=ticket_id)
        db.session.add(row)
        db.session.flush()
    row.seen = (row.seen or 0) + 1
    row.wrong = (row.wrong or 0) + (0 if correct else 1)
    row.right_count = (row.right_count or 0) + (1 if correct else 0)
    row.correct_streak = (row.correct_streak + 1) if correct else 0
    needed = 2 if row.wrong > 0 else 1
    row.mastered = row.correct_streak >= needed
    row.last_seen = utcnow()
    if row.mastered:
        db.session.query(FailedQuestion).filter(
            FailedQuestion.user_id == user_id,
            FailedQuestion.ticket_id == ticket_id,
            FailedQuestion.resolved_at.is_(None),
        ).update({FailedQuestion.resolved_at: utcnow()}, synchronize_session=False)


def overview(user_id: int) -> dict:
    total = db.session.query(func.count(Ticket.id)).scalar() or 0
    stats = db.session.query(
        func.coalesce(func.sum(case((TicketStat.seen > 0, 1), else_=0)), 0).label("seen"),
        func.coalesce(func.sum(case((TicketStat.mastered.is_(True), 1), else_=0)), 0).label("mastered"),
        func.coalesce(
            func.sum(case((and_(TicketStat.wrong > 0, TicketStat.mastered.is_(False)), 1), else_=0)),
            0,
        ).label("pending"),
    ).filter(TicketStat.user_id == user_id).one()

    attempts = db.session.query(
        func.count(Attempt.id),
        func.coalesce(func.sum(case((Attempt.passed.is_(True), 1), else_=0)), 0),
    ).filter(Attempt.user_id == user_id, Attempt.finished_at.isnot(None)).one()

    missing_key = db.session.query(func.count(Ticket.id)).filter(Ticket.correct_index.is_(None)).scalar() or 0
    seen = int(stats.seen)
    mastered = int(stats.mastered)
    pending = int(stats.pending)
    return {
        "total": total,
        "seen": seen,
        "mastered": mastered,
        "pending": pending,
        "unseen": max(total - seen, 0),
        "attempts": int(attempts[0]),
        "passed_attempts": int(attempts[1]),
        "missing_key": missing_key,
        "coverage_pct": round(100 * seen / total) if total else 0,
        "mastered_pct": round(100 * mastered / total) if total else 0,
    }


def exam_cards(user_id: int, cycle: int) -> list[dict]:
    exams = (
        db.session.query(Exam)
        .filter(Exam.user_id == user_id, Exam.cycle == cycle, Exam.kind == "base")
        .order_by(Exam.number)
        .all()
    )
    cards = []
    for exam in exams:
        size = db.session.query(func.count(ExamTicket.ticket_id)).filter(
            ExamTicket.exam_id == exam.id
        ).scalar() or 0
        tries = db.session.query(func.count(Attempt.id)).filter(
            Attempt.exam_id == exam.id, Attempt.finished_at.isnot(None),
        ).scalar() or 0
        best = db.session.query(func.max(Attempt.correct_count)).filter(
            Attempt.exam_id == exam.id, Attempt.finished_at.isnot(None),
        ).scalar()
        ever_passed = db.session.query(func.max(case((Attempt.passed.is_(True), 1), else_=0))).filter(
            Attempt.exam_id == exam.id, Attempt.finished_at.isnot(None),
        ).scalar()
        status = "new"
        if tries:
            status = "passed" if ever_passed else "failed"
        cards.append({
            "id": exam.id, "number": exam.number, "kind": exam.kind,
            "size": size, "tries": tries, "best": best,
            "ever_passed": ever_passed, "status": status,
        })
    return cards


def review_exams(user_id: int, limit: int = 20) -> list[dict]:
    exams = (
        db.session.query(Exam)
        .filter(Exam.user_id == user_id, Exam.kind == "review")
        .order_by(Exam.id.desc())
        .limit(limit)
        .all()
    )
    out = []
    for exam in exams:
        size = db.session.query(func.count(ExamTicket.ticket_id)).filter(
            ExamTicket.exam_id == exam.id
        ).scalar() or 0
        best = db.session.query(func.max(Attempt.correct_count)).filter(
            Attempt.exam_id == exam.id, Attempt.finished_at.isnot(None),
        ).scalar()
        ever_passed = db.session.query(func.max(case((Attempt.passed.is_(True), 1), else_=0))).filter(
            Attempt.exam_id == exam.id, Attempt.finished_at.isnot(None),
        ).scalar()
        tries = db.session.query(func.count(Attempt.id)).filter(
            Attempt.exam_id == exam.id, Attempt.finished_at.isnot(None),
        ).scalar() or 0
        out.append({
            "id": exam.id, "created_at": exam.created_at, "size": size,
            "best": best, "ever_passed": ever_passed, "tries": tries,
        })
    return out


def recent_attempts(user_id: int, limit: int = 6) -> list[dict]:
    rows = (
        db.session.query(Attempt, Exam)
        .join(Exam, Exam.id == Attempt.exam_id)
        .filter(Attempt.user_id == user_id, Attempt.finished_at.isnot(None))
        .order_by(Attempt.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": a.id, "correct_count": a.correct_count, "wrong_count": a.wrong_count,
            "passed": a.passed, "finished_at": a.finished_at,
            "number": e.number, "kind": e.kind,
        }
        for a, e in rows
    ]


def history_attempts(user_id: int, status: str = "all") -> list[dict]:
    q = (
        db.session.query(Attempt, Exam)
        .join(Exam, Exam.id == Attempt.exam_id)
        .filter(Attempt.user_id == user_id, Attempt.finished_at.isnot(None))
    )
    if status == "failed":
        q = q.filter(or_(Attempt.passed.is_(False), Attempt.passed.is_(None)))
    elif status == "passed":
        q = q.filter(Attempt.passed.is_(True))
    rows = q.order_by(Attempt.id.desc()).all()
    return [
        {
            "id": a.id, "correct_count": a.correct_count, "wrong_count": a.wrong_count,
            "passed": a.passed, "finished_at": a.finished_at, "seconds_spent": a.seconds_spent,
            "mode": a.mode, "number": e.number, "kind": e.kind, "exam_id": e.id,
        }
        for a, e in rows
    ]


def list_failed_questions(user_id: int) -> list[dict]:
    rows = (
        db.session.query(Ticket, TicketStat)
        .join(TicketStat, and_(
            TicketStat.ticket_id == Ticket.id, TicketStat.user_id == user_id,
        ))
        .filter(TicketStat.wrong > 0)
        .order_by(TicketStat.mastered.asc(), TicketStat.wrong.desc(), Ticket.id)
        .all()
    )
    return [ticket.to_dict(stats) for ticket, stats in rows]


def bank_page(user_id: int, state: str, query: str, page: int, per_page: int = 25):
    q = (
        db.session.query(Ticket, TicketStat)
        .outerjoin(TicketStat, and_(
            TicketStat.ticket_id == Ticket.id, TicketStat.user_id == user_id,
        ))
    )
    if state == "mastered":
        q = q.filter(TicketStat.mastered.is_(True))
    elif state == "wrong":
        q = q.filter(TicketStat.wrong > 0, TicketStat.mastered.is_(False))
    elif state == "unseen":
        q = q.filter(or_(TicketStat.seen.is_(None), TicketStat.seen == 0))
    if query:
        if query.isdigit():
            q = q.filter(or_(Ticket.question.ilike(f"%{query}%"), Ticket.id == int(query)))
        else:
            q = q.filter(Ticket.question.ilike(f"%{query}%"))

    total = q.count()
    rows = (
        q.order_by(Ticket.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    items = [ticket.to_dict(stats) for ticket, stats in rows]
    return items, total
