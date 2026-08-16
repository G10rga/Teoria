"""SQLAlchemy models for users, the shared ticket bank, and per-user exam history."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from db import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime(timezone=True))

    exams = db.relationship("Exam", back_populates="user", cascade="all, delete-orphan", lazy="dynamic")
    attempts = db.relationship("Attempt", back_populates="user", cascade="all, delete-orphan", lazy="dynamic")
    stats = db.relationship("TicketStat", back_populates="user", cascade="all, delete-orphan", lazy="dynamic")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Ticket(db.Model):
    """Shared question bank (all users see the same tickets)."""
    __tablename__ = "tickets"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(8), nullable=False, default="B")
    question = db.Column(db.Text, nullable=False)
    answers_json = db.Column(db.Text, nullable=False)
    correct_index = db.Column(db.Integer)
    image = db.Column(db.String(255))
    layout = db.Column(db.String(255))
    explanation = db.Column(db.Text)
    source_url = db.Column(db.String(512))
    imported_at = db.Column(db.DateTime(timezone=True))

    @property
    def answers(self) -> list:
        return json.loads(self.answers_json)

    def to_dict(self, stats: "TicketStat | None" = None) -> dict:
        data = {
            "id": self.id,
            "category": self.category,
            "question": self.question,
            "answers": self.answers,
            "correct_index": self.correct_index,
            "image": self.image,
            "layout": self.layout,
            "explanation": self.explanation,
            "source_url": self.source_url,
            "seen": stats.seen if stats else 0,
            "wrong": stats.wrong if stats else 0,
            "right_count": stats.right_count if stats else 0,
            "correct_streak": stats.correct_streak if stats else 0,
            "mastered": bool(stats.mastered) if stats else False,
            "last_seen": stats.last_seen if stats else None,
        }
        return data


class Exam(db.Model):
    """One generated exam belonging to a single user (base cycle or review)."""
    __tablename__ = "exams"
    __table_args__ = (
        db.Index("ix_exams_user_cycle", "user_id", "cycle", "kind"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    cycle = db.Column(db.Integer, nullable=False, default=1)
    number = db.Column(db.Integer)
    kind = db.Column(db.String(16), nullable=False, default="base")  # base | review
    seed = db.Column(db.String(64))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    user = db.relationship("User", back_populates="exams")
    items = db.relationship("ExamTicket", back_populates="exam", cascade="all, delete-orphan",
                            order_by="ExamTicket.position")
    attempts = db.relationship("Attempt", back_populates="exam", cascade="all, delete-orphan")


class ExamTicket(db.Model):
    __tablename__ = "exam_tickets"
    __table_args__ = (
        db.PrimaryKeyConstraint("exam_id", "position"),
        db.Index("ix_exam_tickets_ticket", "ticket_id"),
    )

    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False)

    exam = db.relationship("Exam", back_populates="items")
    ticket = db.relationship("Ticket")


class Attempt(db.Model):
    """A sitting of an exam: score, pass/fail, and timing."""
    __tablename__ = "attempts"
    __table_args__ = (
        db.Index("ix_attempts_user_finished", "user_id", "finished_at"),
        db.Index("ix_attempts_exam", "exam_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    mode = db.Column(db.String(16), nullable=False, default="exam")  # exam | practice
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at = db.Column(db.DateTime(timezone=True))
    correct_count = db.Column(db.Integer)
    wrong_count = db.Column(db.Integer)
    passed = db.Column(db.Boolean)
    seconds_spent = db.Column(db.Integer)

    user = db.relationship("User", back_populates="attempts")
    exam = db.relationship("Exam", back_populates="attempts")
    answers = db.relationship("AttemptAnswer", back_populates="attempt", cascade="all, delete-orphan")

    @property
    def is_failed(self) -> bool:
        return bool(self.finished_at) and not self.passed


class AttemptAnswer(db.Model):
    __tablename__ = "attempt_answers"
    __table_args__ = (
        db.PrimaryKeyConstraint("attempt_id", "ticket_id"),
        db.Index("ix_attempt_answers_wrong", "ticket_id", "is_correct"),
    )

    attempt_id = db.Column(db.Integer, db.ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False)
    chosen_index = db.Column(db.Integer)
    is_correct = db.Column(db.Boolean)
    answered_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    attempt = db.relationship("Attempt", back_populates="answers")
    ticket = db.relationship("Ticket")


class TicketStat(db.Model):
    """Per-user learning state. Review exams are built from rows with wrong > 0 and mastered = 0."""
    __tablename__ = "ticket_stats"
    __table_args__ = (
        db.PrimaryKeyConstraint("user_id", "ticket_id"),
        db.Index("ix_stats_user_wrong", "user_id", "wrong", "mastered"),
    )

    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    seen = db.Column(db.Integer, nullable=False, default=0)
    wrong = db.Column(db.Integer, nullable=False, default=0)
    right_count = db.Column(db.Integer, nullable=False, default=0)
    correct_streak = db.Column(db.Integer, nullable=False, default=0)
    mastered = db.Column(db.Boolean, nullable=False, default=False)
    last_seen = db.Column(db.DateTime(timezone=True))

    user = db.relationship("User", back_populates="stats")
    ticket = db.relationship("Ticket")


class FailedQuestion(db.Model):
    """Every wrong (or blank) answer a user has given — used to drive review exams."""
    __tablename__ = "failed_questions"
    __table_args__ = (
        db.Index("ix_failed_user_open", "user_id", "resolved_at"),
        db.Index("ix_failed_ticket", "ticket_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False)
    chosen_index = db.Column(db.Integer)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    resolved_at = db.Column(db.DateTime(timezone=True))

    user = db.relationship("User")
    ticket = db.relationship("Ticket")
    attempt = db.relationship("Attempt")


class Meta(db.Model):
    __tablename__ = "meta"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text)
