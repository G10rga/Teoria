"""Teoria - Georgian driving theory trainer (Flask).

Local:
    python app.py            # http://127.0.0.1:5000

Production:
    gunicorn wsgi:app
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import or_
from werkzeug.middleware.proxy_fix import ProxyFix

import db as db_utils
import exams
from config import Config
from db import db
from forms import LoginForm, RegisterForm
from models import Attempt, Exam, User, utcnow


def create_app(config_object=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db_utils.init_extensions(app)
    import models  # noqa: F401

    with app.app_context():
        db_utils.init_schema()

    if app.config["SECRET_KEY"] == "dev-only-key":
        app.logger.warning("SECRET_KEY is the default; set SECRET_KEY before deploying.")
        if os.environ.get("FLASK_ENV") == "production":
            raise RuntimeError("Set SECRET_KEY before deploying.")

    app.jinja_env.globals.update(
        MAX_ERRORS=exams.MAX_ERRORS,
        TIME_LIMIT_MIN=exams.TIME_LIMIT_MIN,
        QUESTIONS_PER_EXAM=exams.QUESTIONS_PER_EXAM,
    )

    import admin as admin_mod
    app.register_blueprint(admin_mod.bp)

    @app.context_processor
    def _admin_flag():
        return {"user_is_admin": admin_mod.current_user_is_admin()}

    @app.errorhandler(403)
    def _forbidden(_err):
        return render_template(
            "error.html", code=403,
            message="ამ გვერდზე შესვლა არ გაქვთ.",
        ), 403

    register_routes(app)
    return app


def register_routes(app: Flask) -> None:
    @app.template_filter("ka_date")
    def ka_date(value) -> str:
        if value is None:
            return "—"
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M")
        return str(value).replace("T", " ")[:16]

    @app.get("/health")
    def health():
        return jsonify(ok=True)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        form = RegisterForm()
        if form.validate_on_submit():
            user = User(
                username=form.username.data.strip(),
                email=form.email.data.strip().lower(),
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            db_utils.claim_legacy_data(user.id)
            login_user(user)
            flash("ანგარიში შექმნილია.", "ok")
            return redirect(url_for("dashboard"))
        return render_template("register.html", form=form, active="register")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        form = LoginForm()
        if form.validate_on_submit():
            ident = form.username.data.strip()
            user = User.query.filter(
                or_(User.username == ident, User.email == ident.lower())
            ).one_or_none()
            if user is None or not user.check_password(form.password.data):
                flash("სახელი ან პაროლი არასწორია.", "error")
            else:
                user.last_login_at = utcnow()
                db.session.commit()
                login_user(user)
                next_url = request.args.get("next")
                if next_url and next_url.startswith("/"):
                    return redirect(next_url)
                return redirect(url_for("dashboard"))
        return render_template("login.html", form=form, active="login")

    @app.post("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def dashboard():
        uid = current_user.id
        stats = exams.overview(uid)
        cycle = exams.current_cycle(uid)
        error = None
        if cycle == 0 and stats["total"]:
            try:
                cycle = exams.generate_cycle(uid)
            except RuntimeError as exc:
                error = str(exc)
        cards = exams.exam_cards(uid, cycle) if cycle else []
        reviews = exams.review_exams(uid)
        pending = len(exams.mistake_pool(uid))
        recent = exams.recent_attempts(uid)
        coverage = exams.cycle_coverage(uid, cycle) if cycle else None
        overlaps = exams.cycle_ticket_overlap(uid, cycle) if cycle else []
        return render_template(
            "index.html", stats=stats, cycle=cycle, cards=cards,
            reviews=reviews, pending=pending, recent=recent, error=error,
            coverage=coverage, overlaps=overlaps,
        )

    @app.post("/cycle/new")
    @login_required
    def new_cycle():
        try:
            exams.generate_cycle(current_user.id)
        except RuntimeError as exc:
            flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    @app.post("/review/new")
    @login_required
    def new_review():
        exam_id = exams.create_review_exam(current_user.id)
        if exam_id is None:
            flash("შეცდომების ბილეთები აღარ არის.", "error")
            return redirect(url_for("dashboard"))
        return redirect(url_for("start_exam", exam_id=exam_id), code=307)

    @app.post("/exam/<int:exam_id>/start")
    @login_required
    def start_exam(exam_id: int):
        exam = _owned_exam(exam_id)
        mode = request.form.get("mode", "exam")
        if mode not in ("exam", "practice"):
            mode = "exam"
        attempt_id = exams.start_attempt(current_user.id, exam.id, mode)
        return redirect(url_for("take_attempt", attempt_id=attempt_id))

    @app.get("/attempt/<int:attempt_id>")
    @login_required
    def take_attempt(attempt_id: int):
        attempt = _owned_attempt(attempt_id)
        if attempt.finished_at:
            return redirect(url_for("result", attempt_id=attempt_id))
        exam = db.session.get(Exam, attempt.exam_id)
        ticket_ids = exams.exam_ticket_ids(attempt.exam_id)
        tickets = db_utils.load_tickets(ticket_ids)
        saved = {
            a.ticket_id: a.chosen_index
            for a in attempt.answers
        }
        payload = []
        for t in tickets:
            payload.append({
                "id": t["id"],
                "question": t["question"],
                "answers": t["answers"],
                "correct": t["correct_index"],
                "image": url_for("static", filename=t["image"]) if t["image"] else None,
                "layout": t.get("layout"),
                "source": t["source_url"],
                "chosen": saved.get(t["id"]),
                "explanation": t["explanation"] if attempt.mode == "practice" else None,
            })
        return render_template(
            "exam.html", attempt=attempt, exam=exam,
            questions_json=json.dumps(payload, ensure_ascii=False),
            count=len(payload),
        )

    @app.post("/api/attempt/<int:attempt_id>/answer")
    @login_required
    def api_answer(attempt_id: int):
        _owned_attempt(attempt_id)
        data = request.get_json(silent=True) or {}
        ticket_id = data.get("ticket_id")
        chosen = data.get("chosen_index")
        if ticket_id is None:
            return jsonify({"error": "ticket_id required"}), 400
        ok = exams.save_answer(attempt_id, int(ticket_id),
                               None if chosen is None else int(chosen))
        if ok is None:
            return jsonify({"error": "attempt closed"}), 409
        return jsonify({"saved": True})

    @app.post("/api/attempt/<int:attempt_id>/finish")
    @login_required
    def api_finish(attempt_id: int):
        _owned_attempt(attempt_id)
        data = request.get_json(silent=True) or {}
        seconds = data.get("seconds")
        summary = exams.finish_attempt(attempt_id, seconds)
        summary["redirect"] = url_for("result", attempt_id=attempt_id)
        return jsonify(summary)

    @app.get("/result/<int:attempt_id>")
    @login_required
    def result(attempt_id: int):
        attempt = db.session.get(Attempt, attempt_id)
        if attempt is None:
            abort(404)
        from admin import current_user_is_admin
        if attempt.user_id != current_user.id and not current_user_is_admin():
            abort(404)
        viewing_other = attempt.user_id != current_user.id
        if attempt.finished_at is None:
            if viewing_other:
                abort(404)
            exams.finish_attempt(attempt_id)
            db.session.refresh(attempt)
        exam = db.session.get(Exam, attempt.exam_id)
        ticket_ids = exams.exam_ticket_ids(attempt.exam_id)
        tickets = db_utils.load_tickets(ticket_ids)
        answers = {a.ticket_id: a for a in attempt.answers}
        pending = 0 if viewing_other else len(exams.mistake_pool(current_user.id))
        next_exam = None
        if not viewing_other and exam and exam.kind == "base" and exam.number:
            next_exam = (
                Exam.query.filter(
                    Exam.user_id == current_user.id,
                    Exam.cycle == exam.cycle,
                    Exam.kind == "base",
                    Exam.number > exam.number,
                ).order_by(Exam.number).first()
            )
        rows = [
            {
                "ticket": t,
                "chosen": answers[t["id"]].chosen_index if t["id"] in answers else None,
                "is_correct": bool(answers[t["id"]].is_correct) if t["id"] in answers else False,
            }
            for t in tickets
        ]
        return render_template("result.html", attempt=attempt, exam=exam, rows=rows,
                               pending=pending, next_exam=next_exam,
                               viewing_other=viewing_other)

    @app.get("/mistakes")
    @login_required
    def mistakes():
        items = exams.list_failed_questions(current_user.id)
        pending = len(exams.mistake_pool(current_user.id))
        return render_template("mistakes.html", items=items, pending=pending)

    @app.get("/history")
    @login_required
    def history():
        status = request.args.get("status", "all")
        if status not in ("all", "passed", "failed"):
            status = "all"
        items = exams.history_attempts(current_user.id, status)
        pending = len(exams.mistake_pool(current_user.id))
        return render_template("history.html", items=items, status=status, pending=pending)

    @app.get("/bank")
    @login_required
    def bank():
        state = request.args.get("state", "all")
        query = (request.args.get("q") or "").strip()
        page = max(int(request.args.get("page", 1) or 1), 1)
        items, total = exams.bank_page(current_user.id, state, query, page)
        per_page = 25
        return render_template(
            "bank.html", items=items, total=total, page=page,
            pages=max((total + per_page - 1) // per_page, 1),
            state=state, query=query,
        )

    def _owned_exam(exam_id: int) -> Exam:
        exam = db.session.get(Exam, exam_id)
        if exam is None or exam.user_id != current_user.id:
            abort(404)
        return exam

    def _owned_attempt(attempt_id: int) -> Attempt:
        attempt = db.session.get(Attempt, attempt_id)
        if attempt is None or attempt.user_id != current_user.id:
            abort(404)
        return attempt


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "1") != "0",
            port=int(os.environ.get("PORT", 5000)))
