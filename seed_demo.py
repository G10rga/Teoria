"""Fill the DB with 921 synthetic tickets so the UI can be tested without scraping.

    python seed_demo.py          # creates prava.db with demo data + cycle 1

Replace it later with real data: delete prava.db and run scraper.py scrape.
"""
from __future__ import annotations

import random

import db
import exams

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
    with db.connect() as conn:
        conn.execute("DELETE FROM exam_tickets")
        conn.execute("DELETE FROM attempt_answers")
        conn.execute("DELETE FROM attempts")
        conn.execute("DELETE FROM exams")
        conn.execute("DELETE FROM ticket_stats")
        conn.execute("DELETE FROM tickets")
        for i in range(1, count + 1):
            n_answers = rng.choice([2, 3, 3, 4])
            answers = [f"{OPTIONS[j % len(OPTIONS)]} ({j + 1})" for j in range(n_answers)]
            db.upsert_ticket(conn, {
                "id": i,
                "category": "B",
                "question": f"{STEMS[i % len(STEMS)]}",
                "answers": answers,
                "correct_index": rng.randrange(n_answers),
                "image": None,
                "explanation": EXPLANATION,
                "source_url": f"https://teoria.on.ge/tickets?ticket={i}",
            })
        conn.commit()
        cycle = exams.generate_cycle(conn)
    print(f"seeded {count} demo tickets, generated cycle {cycle}")


if __name__ == "__main__":
    main()
