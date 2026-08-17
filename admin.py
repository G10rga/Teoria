"""Admin dashboard: users, their progress, and ticket editing."""
from __future__ import annotations

import json
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import func, or_

import exams
from db import db
from forms import TicketEditForm
from models import Ticket, User

bp = Blueprint("admin", __name__, url_prefix="/admin")


def current_user_is_admin() -> bool:
    if not current_user.is_authenticated:
        return False
    names = current_app.config.get("ADMIN_USERNAMES") or frozenset()
    if current_user.username.lower() in names:
        return True
    return bool(getattr(current_user, "is_admin", False))


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user_is_admin():
            abort(403)
        return fn(*args, **kwargs)
    return wrapped


@bp.get("/")
@admin_required
def home():
    q = (request.args.get("q") or "").strip()
    users_q = User.query
    if q:
        like = f"%{q}%"
        users_q = users_q.filter(or_(User.username.ilike(like), User.email.ilike(like)))
    users = users_q.order_by(User.created_at.desc()).all()
    rows = []
    for user in users:
        stats = exams.overview(user.id)
        rows.append({"user": user, "stats": stats})
    ticket_total = db.session.query(func.count(Ticket.id)).scalar() or 0
    missing_key = db.session.query(func.count(Ticket.id)).filter(
        Ticket.correct_index.is_(None)
    ).scalar() or 0
    user_total = User.query.count()
    return render_template(
        "admin/users.html",
        rows=rows, query=q,
        user_count=user_total,
        ticket_total=ticket_total,
        missing_key=missing_key,
        active="admin",
        admin_tab="users",
    )


@bp.get("/users/<int:user_id>")
@admin_required
def user_detail(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    stats = exams.overview(user.id)
    cycle = exams.current_cycle(user.id)
    cards = exams.exam_cards(user.id, cycle) if cycle else []
    attempts = exams.history_attempts(user.id, "all")[:30]
    mistakes = exams.list_failed_questions(user.id)[:40]
    pending = len(exams.mistake_pool(user.id))
    return render_template(
        "admin/user.html",
        user=user, stats=stats, cycle=cycle, cards=cards,
        attempts=attempts, mistakes=mistakes, pending=pending,
        active="admin", admin_tab="users",
    )


@bp.get("/tickets")
@admin_required
def tickets():
    q = (request.args.get("q") or "").strip()
    state = request.args.get("state", "all")
    page = max(int(request.args.get("page", 1) or 1), 1)
    per_page = 40
    query = Ticket.query
    if q:
        if q.isdigit():
            query = query.filter(or_(Ticket.id == int(q), Ticket.question.ilike(f"%{q}%")))
        else:
            query = query.filter(Ticket.question.ilike(f"%{q}%"))
    if state == "missing":
        query = query.filter(Ticket.correct_index.is_(None))
    total = query.count()
    items = (
        query.order_by(Ticket.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    pages = max((total + per_page - 1) // per_page, 1)
    return render_template(
        "admin/tickets.html",
        items=items, total=total, page=page, pages=pages,
        query=q, state=state, active="admin", admin_tab="tickets",
    )


@bp.route("/tickets/<int:ticket_id>", methods=["GET", "POST"])
@admin_required
def edit_ticket(ticket_id: int):
    ticket = db.session.get(Ticket, ticket_id)
    if ticket is None:
        abort(404)
    form = TicketEditForm()
    if form.validate_on_submit():
        answers = form.answers_list()
        if len(answers) < 2:
            flash("მინიმუმ ორი პასუხი უნდა იყოს.", "error")
        else:
            raw = (form.correct_index.data or "").strip()
            correct = int(raw) if raw.isdigit() else None
            if correct is not None and not (0 <= correct < len(answers)):
                flash("სწორი პასუხის ნომერი არჩეულ პასუხებს არ ემთხვევა.", "error")
            else:
                ticket.question = (form.question.data or "").strip()
                ticket.answers_json = json.dumps(answers, ensure_ascii=False)
                ticket.correct_index = correct
                ticket.explanation = (form.explanation.data or "").strip() or None
                image = (form.image.data or "").strip()
                ticket.image = image or ticket.image
                db.session.commit()
                flash(f"ბილეთი #{ticket.id} განახლდა.", "ok")
                return redirect(url_for("admin.edit_ticket", ticket_id=ticket.id))
    elif request.method == "GET":
        form.question.data = ticket.question
        answers = ticket.answers
        for i, text in enumerate(answers[:4], start=1):
            getattr(form, f"answer_{i}").data = text
        form.correct_index.data = "" if ticket.correct_index is None else str(ticket.correct_index)
        form.explanation.data = ticket.explanation or ""
        form.image.data = ticket.image or ""
    return render_template(
        "admin/ticket_edit.html",
        form=form, ticket=ticket,         active="admin", admin_tab="tickets",
    )
