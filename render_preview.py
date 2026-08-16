"""Render the Jinja templates to static HTML for visual review (no Flask needed).

    python render_preview.py    # writes preview_*.html next to the templates
"""
from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

BASE = Path(__file__).resolve().parent
env = Environment(loader=FileSystemLoader(BASE / "templates"))


def url_for(endpoint, **values):
    if endpoint == "static":
        return "static/" + values["filename"]
    return "#"


class Anon:
    is_authenticated = False
    username = ""


env.globals.update(
    url_for=url_for, MAX_ERRORS=3, TIME_LIMIT_MIN=30, QUESTIONS_PER_EXAM=30,
    csrf_token=lambda: "", current_user=Anon(), get_flashed_messages=lambda **_: [],
)
env.filters["ka_date"] = lambda v: (v or "").replace("T", " ")[:16] or "-"

QUESTION = (
    "რომელი სატრანსპორტო საშუალების მძღოლის მიმართ წარმოექმნება გზის დათმობის "
    "ვალდებულება მსუბუქი ავტომობილის მძღოლს ისრის მიმართულებით მოძრაობის შემთხვევაში?"
)
ANSWERS = [
    "მხოლოდ მოტოციკლის მძღოლის მიმართ",
    "მხოლოდ ავტობუსის მძღოლის მიმართ",
    "როგორც მოტოციკლის, ასევე ავტობუსის მძღოლის მიმართ",
]
EXPLANATION = (
    "„საგზაო მოძრაობის შესახებ“ საქართველოს კანონის 36-ე მუხლის მე-4 პუნქტის „ა“ ქვეპუნქტის "
    "თანახმად, არარეგულირებულ გზაჯვარედინზე მეორეხარისხოვან გზაზე მოძრავი სატრანსპორტო "
    "საშუალების მძღოლი ვალდებულია გზა დაუთმოს მთავარი გზიდან მოახლოებულ სატრანსპორტო საშუალებას."
)


class Obj(dict):
    __getattr__ = dict.get


def ticket(i: int) -> dict:
    return {
        "id": i, "question": QUESTION, "answers": ANSWERS, "correct_index": 2,
        "image": "tickets/demo.jpg" if i % 2 else None,
        "layout": "answers-num-3 big-answers cutoff-2 ticket-container-small",
        "explanation": EXPLANATION, "seen": 3, "wrong": 2, "mastered": 0,
    }


def main() -> None:
    stats = {"total": 921, "seen": 615, "mastered": 498, "pending": 62, "unseen": 306,
             "attempts": 23, "passed_attempts": 17, "missing_key": 0,
             "coverage_pct": 67, "mastered_pct": 54}
    cards = []
    for n in range(1, 32):
        size = 30 if n < 31 else 21
        if n <= 14:
            cards.append(Obj(id=n, number=n, size=size, tries=1, best=29,
                             ever_passed=1, status="passed"))
        elif n <= 17:
            cards.append(Obj(id=n, number=n, size=size, tries=2, best=25,
                             ever_passed=0, status="failed"))
        else:
            cards.append(Obj(id=n, number=n, size=size, tries=0, best=None,
                             ever_passed=None, status="new"))
    reviews = [Obj(id=42, created_at="2026-08-16T18:20:00", size=30, best=27, ever_passed=0, tries=1)]
    recent = [Obj(id=9, correct_count=28, wrong_count=2, passed=1,
                  finished_at="2026-08-16T19:02:00", number=14, kind="base"),
              Obj(id=8, correct_count=25, wrong_count=5, passed=0,
                  finished_at="2026-08-15T21:41:00", number=17, kind="base")]

    pages = {
        "preview_index.html": ("index.html", dict(stats=stats, cycle=1, cards=cards,
                                                  reviews=reviews, pending=62, recent=recent)),
        "preview_exam.html": ("exam.html", dict(
            attempt=Obj(id=1, mode="practice"), exam=Obj(id=1, number=14, kind="base"),
            count=30,
            questions_json=json.dumps([
                {**ticket(i), "correct": 2, "chosen": (2 if i % 3 else 0) if i < 12 else None,
                 "source": "#"} for i in range(1, 31)
            ], ensure_ascii=False))),
        "preview_result.html": ("result.html", dict(
            attempt=Obj(id=1, correct_count=27, wrong_count=3, passed=1, seconds_spent=742),
            exam=Obj(id=1, number=14, kind="base"),
            rows=[{"ticket": ticket(i), "chosen": 2 if i % 3 else 1,
                   "is_correct": bool(i % 3)} for i in range(1, 6)],
            pending=62, next_exam=Obj(id=15, number=15))),
        "preview_mistakes.html": ("mistakes.html", dict(
            items=[ticket(i) for i in (14, 88, 203, 415, 690)], pending=62)),
        "preview_bank.html": ("bank.html", dict(
            items=[{**ticket(i), "mastered": i % 3 == 0, "wrong": i % 2, "seen": 1}
                   for i in range(1, 11)],
            total=921, page=1, pages=37, state="all", query="")),
    }
    for out, (template, ctx) in pages.items():
        (BASE / out).write_text(env.get_template(template).render(**ctx), encoding="utf-8")
        print("wrote", out)


if __name__ == "__main__":
    main()
