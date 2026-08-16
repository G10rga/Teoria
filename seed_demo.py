"""Fill the DB with 921 synthetic tickets so the UI can be tested without scraping.

    python seed_demo.py          # creates prava.db with demo tickets

Then register in the UI; the first visit generates your personal exam cycle.
Replace later with real data: delete prava.db and run scraper.py scrape.
"""
from __future__ import annotations

import random

import db
from models import Attempt, AttemptAnswer, Exam, ExamTicket, FailedQuestion, Ticket, TicketStat

STEMS = [
    "რომელი ავტომობილის მძღოლს წარმოექმნება გზის დათმობის ვალდებულება?",
    "რა თანამიმდევრობით უნდა გაიარონ გზაჯვარედინი ავტომობილებმა?",
    "დაშვებულია თუ არა მძღოლს მობრუნების მანევრის შესრულება ამ ადგილას?",
    "რომელი საგზაო ნიშანი კრძალავს გასწრებას ამ მონაცვეთზე?",
    "რა სიჩქარით ექნება ნებადართული მოძრაობა დასახლებულ 10კმ-იან ზონაში?",
]
OPTIONS = [
    "ვალდებულია",
    "არ არის ვალდებული",
    "მხოლოდ დასახლებულ შემთხვევაში",
    "მხოლოდ საგზაო ნიშნის არსებობისას",
]
EXPLANATION = (
    "„საგზაო მოძრაობის შესახებ“ საქართველოს კანონის 36-ე მუხლის მე-4 პუნქტის თანახმად, "
    "არარეგულირებულ გზაჯვარედინზე მძღოლი ვალდებულია გზა დაუთმოს მარჯვნიდან მოახლოებულ სატრანსპორტო საშუალებას."
)


def main(count: int = 921) -> None:
    rng = random.Random(42)
    db.init_db()
    with db.connect() as session:
        session.query(FailedQuestion).delete()
        session.query(AttemptAnswer).delete()
        session.query(Attempt).delete()
        session.query(ExamTicket).delete()
        session.query(Exam).delete()
        session.query(TicketStat).delete()
        session.query(Ticket).delete()
        for i in range(1, count + 1):
            n_answers = rng.choice([2, 3, 3, 4])
            answers = [f"{OPTIONS[j % len(OPTIONS)]} ({j + 1})" for j in range(n_answers)]
            db.upsert_ticket({
                "id": i,
                "category": "B",
                "question": f"{STEMS[i % len(STEMS)]}",
                "answers": answers,
                "correct_index": rng.randrange(n_answers),
                "image": None,
                "explanation": EXPLANATION,
                "source_url": f"https://teoria.on.ge/tickets?ticket={i}",
            }, session=session)
    print(f"seeded {count} demo tickets — register in the app to generate cycle 1")


if __name__ == "__main__":
    main()
