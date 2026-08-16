"""Prava - Georgian driving theory trainer (Flask).

Run:
    python app.py            # http://127.0.0.1:5000
"""
from __future__ import annotations

import json
import os

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   url_for)

import db
import exams

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("PRAVA_SECRET", "dev-only-key")
app.jinja_env.globals.update(
    MAX_ERRORS=exams.MAX_ERRORS,
    TIME_LIMIT_MIN=exams.TIME_LIMIT_MIN,
    QUESTIONS_PER_EXAM=exams.QUESTIONS_PER_EXAM,
)


@app.before_request
def ensure_db() -> None:
    db.init_db()


# ------------------------------------------------------------------ dashboard
@app.route("/")
def dashboard():
    with db.connect() as conn:
        stats = exams.overview(conn)
        cycle = exams.current_cycle(conn)
        if cycle == 0 and stats["total"]:
            cycle = exams.generate_cycle(conn)
        cards = exams.exam_cards(conn, cycle) if cycle else []
        reviews = exams.review_exams(conn)
        pending = len(exams.mistake_pool(conn))
        recent = conn.execute(
            """
            SELECT a.id, a.correct_count, a.wrong_count, a.passed, a.finished_at,
                   e.number, e.kind
            FROM attempts a JOIN exams e ON e.id = a.exam_id
            WHERE a.finished_at IS NOT NULL
            ORDER BY a.id DESC LIMIT 6
            """
        ).fetchall()
    return render_template(
        "index.html", stats=stats, cycle=cycle, cards=cards,
        reviews=reviews, pending=pending, recent=recent,
    )


@app.post("/cycle/new")
def new_cycle():
    with db.connect() as conn:
        exams.generate_cycle(conn)
    return redirect(url_for("dashboard"))


@app.post("/review/new")
def new_review():
    with db.connect() as conn:
        exam_id = exams.create_review_exam(conn)
    if exam_id is None:
        return redirect(url_for("dashboard"))
    return redirect(url_for("start_exam", exam_id=exam_id), code=307) \
        if request.method == "POST" else redirect(url_for("dashboard"))


# ----------------------------------------------------------------- exam flow
@app.post("/exam/<int:exam_id>/start")
def start_exam(exam_id: int):
    mode = request.form.get("mode", "exam")
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM exams WHERE id = ?", (exam_id,)).fetchone():
            abort(404)
        attempt_id = exams.start_attempt(conn, exam_id, mode)
    return redirect(url_for("take_attempt", attempt_id=attempt_id))


@app.get("/attempt/<int:attempt_id>")
def take_attempt(attempt_id: int):
    with db.connect() as conn:
        attempt = conn.execute(
            "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        if attempt is None:
            abort(404)
        if attempt["finished_at"]:
            return redirect(url_for("result", attempt_id=attempt_id))
        exam = conn.execute(
            "SELECT * FROM exams WHERE id = ?", (attempt["exam_id"],)
        ).fetchone()
        ticket_ids = exams.exam_ticket_ids(conn, attempt["exam_id"])
        tickets = db.load_tickets(conn, ticket_ids)
        saved = {
            r["ticket_id"]: r["chosen_index"]
            for r in conn.execute(
                "SELECT ticket_id, chosen_index FROM attempt_answers WHERE attempt_id = ?",
                (attempt_id,),
            )
        }
    payload = [
        {
            "id": t["id"],
            "question": t["question"],
            "answers": t["answers"],
            "correct": t["correct_index"],
            "image": url_for("static", filename=t["image"]) if t["image"] else None,
            "layout": t.get("layout"),
            "explanation": t["explanation"],
            "source": t["source_url"],
            "chosen": saved.get(t["id"]),
        }
        for t in tickets
    ]
    return render_template(
        "exam.html", attempt=attempt, exam=exam,
        questions_json=json.dumps(payload, ensure_ascii=False),
        count=len(payload),
    )


@app.post("/api/attempt/<int:attempt_id>/answer")
def api_answer(attempt_id: int):
    data = request.get_json(silent=True) or {}
    ticket_id = data.get("ticket_id")
    chosen = data.get("chosen_index")
    if ticket_id is None:
        return jsonify({"error": "ticket_id required"}), 400
    with db.connect() as conn:
        ok = exams.save_answer(conn, attempt_id, int(ticket_id),
                               None if chosen is None else int(chosen))
    return jsonify({"saved": True, "correct": ok})


@app.post("/api/attempt/<int:attempt_id>/finish")
def api_finish(attempt_id: int):
    data = request.get_json(silent=True) or {}
    with db.connect() as conn:
        summary = exams.finish_attempt(conn, attempt_id, data.get("seconds"))
    summary["redirect"] = url_for("result", attempt_id=attempt_id)
    return jsonify(summary)


@app.get("/result/<int:attempt_id>")
def result(attempt_id: int):
    with db.connect() as conn:
        attempt = conn.execute(
            "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        if attempt is None:
            abort(404)
        if attempt["finished_at"] is None:
            exams.finish_attempt(conn, attempt_id)
            attempt = conn.execute(
                "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        exam = conn.execute(
            "SELECT * FROM exams WHERE id = ?", (attempt["exam_id"],)
        ).fetchone()
        ticket_ids = exams.exam_ticket_ids(conn, attempt["exam_id"])
        tickets = db.load_tickets(conn, ticket_ids)
        answers = {
            r["ticket_id"]: r
            for r in conn.execute(
                "SELECT * FROM attempt_answers WHERE attempt_id = ?", (attempt_id,)
            )
        }
        pending = len(exams.mistake_pool(conn))
        next_exam = conn.execute(
            "SELECT id, number FROM exams WHERE cycle = ? AND kind = 'base' AND number > ? "
            "ORDER BY number LIMIT 1",
            (exam["cycle"], exam["number"] or 0),
        ).fetchone()
    rows = [
        {
            "ticket": t,
            "chosen": answers.get(t["id"], {})["chosen_index"] if t["id"] in answers else None,
            "is_correct": bool(answers[t["id"]]["is_correct"]) if t["id"] in answers else False,
        }
        for t in tickets
    ]
    return render_template("result.html", attempt=attempt, exam=exam, rows=rows,
                           pending=pending, next_exam=next_exam)


# ------------------------------------------------------------ mistakes / bank
@app.get("/mistakes")
def mistakes():
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT t.*, s.wrong, s.seen, s.correct_streak, s.mastered, s.last_seen
            FROM ticket_stats s JOIN tickets t ON t.id = s.ticket_id
            WHERE s.wrong > 0
            ORDER BY s.mastered ASC, s.wrong DESC, t.id
            """
        ).fetchall()
        pending = len(exams.mistake_pool(conn))
    items = [{**db.ticket_row_to_dict(r)} for r in rows]
    return render_template("mistakes.html", items=items, pending=pending)


@app.get("/bank")
def bank():
    state = request.args.get("state", "all")
    query = (request.args.get("q") or "").strip()
    page = max(int(request.args.get("page", 1)), 1)
    per_page = 25

    where, params = [], []
    if state == "mastered":
        where.append("s.mastered = 1")
    elif state == "wrong":
        where.append("s.wrong > 0 AND s.mastered = 0")
    elif state == "unseen":
        where.append("s.seen = 0")
    if query:
        where.append("(t.question LIKE ? OR t.id = ?)")
        params += [f"%{query}%", query if query.isdigit() else -1]
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    with db.connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) c FROM tickets t JOIN ticket_stats s ON s.ticket_id = t.id {clause}",
            params,
        ).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT t.*, s.seen, s.wrong, s.mastered
            FROM tickets t JOIN ticket_stats s ON s.ticket_id = t.id
            {clause}
            ORDER BY t.id LIMIT ? OFFSET ?
            """,
            [*params, per_page, (page - 1) * per_page],
        ).fetchall()
    items = [db.ticket_row_to_dict(r) for r in rows]
    return render_template("bank.html", items=items, total=total, page=page,
                           pages=max((total + per_page - 1) // per_page, 1),
                           state=state, query=query)


@app.template_filter("ka_date")
def ka_date(value: str | None) -> str:
    if not value:
        return "—"
    return value.replace("T", " ")[:16]


if __name__ == "__main__":
    db.init_db()
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
