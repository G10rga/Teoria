"""Offline sanity test for the parser: synthetic HTML in the real site's markup.

    python test_parser.py
"""
from __future__ import annotations

from pathlib import Path

import scraper

BLOCK = """
<div class="item">
  <div class="ticket-container locale-ka  hovering ticket-container-small cutoff-1"
       {key_attr}>
    <div class="text-content">
      <div class="t-header"><span class="t-num">#{tid}</span></div>
      <div class="t-question text-wrap">რომელი მძღოლი ვალდებულია დაუთოს გზა ამ გზაგებაზე?</div>
      <img src="/uploads/tickets/{tid}.jpg">
      <img src="/img/admixer-banner.png">
      <div class="t-answers">
        {answers}
      </div>
      <div class="t-explanation text-wrap">
        <b>ბილეთის განმარტება:</b>
        საქართველოს კანონის 36-ე მუხლის მე-4 პუნქტის თანახმად.
        <a href="https://teoria.on.ge/tickets?ticket={tid}">ბილეთის გვერდი</a>
      </div>
      <a class="pull-right goto-ticket" href="/tickets?ticket={tid}">
        <span class="glyphicon glyphicon-share-alt"></span></a>
    </div>
  </div>
</div>
"""

ANSWER = """
<div class="t-answer t-answer-{n}" {item_attr}>
  <div class="t-answer-inner">
    <span class="t-a-num">{n}</span>
    <span class="t-a-text">პასუხი ნომერი {n} — ტექსტი და დეტალები</span>
  </div>
</div>
"""


def _blocks(key_attr: str, item_attr_for: int | None, count: int,
            ids=(1, 2, 4)) -> str:
    answers = "".join(
        ANSWER.format(n=n, item_attr=('data-is-correct="1"'
                                      if item_attr_for == n else ""))
        for n in range(1, count + 1)
    )
    return "".join(BLOCK.format(tid=tid, key_attr=key_attr, answers=answers)
                   for tid in ids)


def page(key_attr: str, item_attr_for: int | None = None, count: int = 4) -> str:
    blocks = _blocks(key_attr, item_attr_for, count)
    return f"<html><body><div class='tickets-list with-topics'>{blocks}</div></body></html>"


def page_shared_wrapper(key_attr: str, item_attr_for: int | None = None,
                        count: int = 4) -> str:
    """Regression case for the real site: the LIST WRAPPER carries the same
    `ticket-container` class as the individual ticket blocks, and the page has
    chrome (category dropdown, paginator) with options of its own.

    A class-anchored parser collapses this into one giant 'ticket' whose
    question is the dropdown label and whose answers are every option on the
    page. That is exactly the bug this test exists to prevent.
    """
    blocks = _blocks(key_attr, item_attr_for, count)
    chrome = """
    <div class="on-pagination clearfix">
      <label>აირჩიეთ კატეგორია:</label>
      <select class="form-control paginator-select">
        <option value="1">A</option><option value="2">B, B1</option>
        <option value="3">C</option><option value="4">D</option>
      </select>
      <a href="/tickets/2?page=2">შემდეგი</a>
    </div>
    """
    return ("<html><body>"
            "<div class='ticket-container locale-ka tickets-list with-topics'>"
            f"{chrome}{blocks}{chrome}"
            "</div></body></html>")


def page_no_container_class(key_attr: str, count: int = 4) -> str:
    """Tickets separated only by their answer group and ticket link - no
    per-ticket wrapper class to anchor on at all."""
    answers = "".join(ANSWER.format(n=n, item_attr="")
                      for n in range(1, count + 1))
    parts = []
    for tid in (1, 2, 4):
        parts.append(
            f'<section><h3>#{tid}</h3>'
            f'<p>რომელი მძღოლი ვალდებულია დაუთოს გზა ამ გზაგებაზე?</p>'
            f'<div {key_attr}>{answers}</div>'
            f'<a href="/tickets?ticket={tid}">ბილეთის გვერდი</a></section>'
        )
    return f"<html><body><main>{''.join(parts)}</main></body></html>"


CASES = [
    ("comma flags 0,0,1,0", 'data-is-correct-list="0,0,1,0"', None, 4, 2),
    ("bool flags", 'data-is-correct-list="false,true,false,false"', None, 4, 1),
    ("json array", "data-is-correct-list='[0,1,0,0]'", None, 4, 1),
    ("pipe separated", 'data-is-correct-list="no|no|no|yes"', None, 4, 3),
    ("bare 1-based index", 'data-is-correct-list="3"', None, 4, 2),
    ("three answers", 'data-is-correct-list="0,1,0"', None, 3, 1),
    ("per-answer attribute", "", 4, 4, 3),
    ("no key at all", "", None, 4, None),
]


def main() -> None:
    failures = 0
    for label, key_attr, item_attr, count, expected in CASES:
        tickets = scraper.parse_html(page(key_attr, item_attr, count), "B")
        ok = (len(tickets) == 3
              and all(len(t["answers"]) == count for t in tickets)
              and all(t["correct_index"] == expected for t in tickets))
        first = tickets[0] if tickets else {}
        print(f"[{'ok ' if ok else 'FAIL'}] {label}: "
              f"{len(tickets)} tickets, correct={first.get('correct_index')} "
              f"(expected {expected}), answers={len(first.get('answers', []))}")
        if not ok:
            failures += 1
            print("       ", first)

    # --- regression: shared wrapper class must not merge the page into one ---
    for label, html in (
        ("shared wrapper class",
         page_shared_wrapper('data-is-correct-list="0,0,1,0"')),
        ("no per-ticket class",
         page_no_container_class('data-is-correct-list="0,0,1,0"')),
    ):
        tickets = scraper.parse_html(html, "B")
        ok = (len(tickets) == 3
              and [t["id"] for t in tickets] == [1, 2, 4]
              and all(len(t["answers"]) == 4 for t in tickets)
              and all(t["correct_index"] == 2 for t in tickets)
              and all("კატეგორია" not in t["question"] for t in tickets))
        print(f"[{'ok ' if ok else 'FAIL'}] {label}: {len(tickets)} tickets, "
              f"answers={[len(t['answers']) for t in tickets]}, "
              f"ids={[t['id'] for t in tickets]}")
        if not ok:
            failures += 1
            for t in tickets:
                print("        ", t["id"], t["question"][:60],
                      len(t["answers"]), t["correct_index"])

    # --- the real thing: one verbatim ticket block captured from the site ---
    fixture = Path(__file__).resolve().parent / "fixtures" / "real_ticket.html"
    if fixture.exists():
        real = scraper.parse_html(fixture.read_text(encoding="utf-8"), "B")
        print("\nreal ticket fixture")
        if len(real) != 1:
            print(f"[FAIL] expected 1 ticket, got {len(real)}")
            failures += 1
        else:
            t = real[0]
            for field in ("id", "question", "answers", "correct_index",
                          "image_url", "layout", "explanation"):
                value = t[field]
                text = value if isinstance(value, (int, type(None))) else str(value)
                print(f"  {field}: {text[:110] if isinstance(text, str) else text}")
            real_checks = {
                "id is 1": t["id"] == 1,
                "2 answers (ans-empty dropped)": len(t["answers"]) == 2,
                "correct is option 2": t["correct_index"] == 1,
                "question is the question": t["question"].endswith("?"),
                "answer text clean": t["answers"][0] == "სატვირთო ავტომობილის მძღოლს",
                "image found": bool(t["image_url"]) and t["image_url"].endswith(".jpg"),
                "explanation clean": bool(t["explanation"])
                and "36-ე" in t["explanation"]
                and "ბილეთის გვერდი" not in t["explanation"],
                "layout captured": bool(t.get("layout"))
                and "answers-num-" in t["layout"]
                and "cutoff-" in t["layout"],
            }
            for name, passed in real_checks.items():
                print(f"[{'ok ' if passed else 'FAIL'}] {name}")
                failures += 0 if passed else 1
    else:
        print("\n[skip] fixtures/real_ticket.html missing")

    sample = scraper.parse_html(page('data-is-correct-list="0,0,1,0"'), "B")[0]
    print("\nsample ticket")
    for field in ("id", "question", "answers", "correct_index", "image_url",
                  "explanation", "source_url"):
        print(f"  {field}: {sample[field]}")
    checks = {
        "id": sample["id"] == 1,
        "question ends with ?": sample["question"].endswith("?"),
        "question has no answer text": "პასუხი ნომერი" not in sample["question"],
        "answers clean of numbering": not sample["answers"][0][0].isdigit(),
        "image skips the ad": sample["image_url"].endswith("/uploads/tickets/1.jpg"),
        "explanation captured": bool(sample["explanation"])
        and "36-ე" in sample["explanation"],
    }
    for name, passed in checks.items():
        print(f"[{'ok ' if passed else 'FAIL'}] {name}")
        failures += 0 if passed else 1

    print(f"\n{failures} failures")


if __name__ == "__main__":
    main()
