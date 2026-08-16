# Teoria — B/B1 theory trainer

A local Flask app that turns the 921 category-2 (B, B1) tickets from teoria.on.ge into
**31 exams that partition the whole bank**: every ticket appears in exactly one exam
(30 exams x 30 questions + 1 exam x 21 questions). No repeats, no gaps.
On top of that it tracks every wrong answer and builds review exams out of them until
all 921 tickets are answered correctly.

## Setup

```bash
cd teoria
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 1. Get the tickets

```bash
python scraper.py scrape --pages 47 --delay 1.0   # download + parse all 47 pages
python scraper.py check                            # report tickets missing a correct answer
```

The scraper caches raw HTML in `html_cache/`, so re-parsing is free:

```bash
python scraper.py parse            # re-parse from cache after changing the parser
python scraper.py inspect --page 1 # dump one ticket block to inspect_block.html
```

The parser targets the site's real markup: each ticket is a `div.ticket-container`, options
are `.t-answer` / `.t-a-num` / `.t-a-text`, and **the answer key is the
`data-is-correct-list` attribute on the ticket block** (one per ticket). No manual key
needed. `python test_parser.py` runs offline unit tests over that markup, including several
attribute encodings (`0,0,1,0`, `false,true,...`, `[0,1,0,0]`, `no|yes`, bare index).

Fallback if the site never exposes the answer in HTML: put them in a CSV and import it.

```csv
ticket_id,correct_index
1,2
2,0
```

```bash
python scraper.py answers key.csv   # 0-based; 1-based values are auto-detected
```

### 2. Try the UI without scraping

```bash
python seed_demo.py    # 921 synthetic tickets + cycle 1
```

### 3. Run

```bash
python app.py          # http://127.0.0.1:5000
```

Env vars: `teoria_DB` (default `teoria.db`), `teoria_SECRET`, `PORT`.

## How the randomness works

`exams.generate_cycle()` takes all 921 ticket ids, shuffles them **once** with
`random.Random(seed).shuffle`, then slices the shuffled list into consecutive chunks of 30.
Because it is a partition of a permutation, a ticket cannot appear twice and cannot be
skipped. Finishing all 31 exams = seeing all 921 tickets exactly once.

"ახალი ციკლი" (new cycle) reshuffles and creates the next 31-exam set, keeping your
history and statistics.

## Review loop

- Every answer is stored per ticket: `seen`, `wrong`, `right_count`, `correct_streak`, `mastered`.
- A ticket becomes **დაუჭერი (mastered)** after 1 correct answer if you never missed it,
  or after 2 consecutive correct answers if you have missed it before.
- „შეცდომების გამოცდა“ builds a 30-question exam from the mistake pool, worst first
  (most wrong answers, lowest streak, least recently seen). If fewer than 30 mistakes remain,
  it tops up with tickets you have never seen.
- The dashboard ring shows coverage of the whole 921-ticket bank, not just the current cycle.

## Exam rules

- 30 questions, 30-minute countdown, auto-submit at 0.
- 3 or fewer mistakes = pass (same as the real exam). Unanswered counts as wrong.
- `ვარჯიში` (practice) mode shows the correct answer and the official explanation instantly;
  exam mode reveals everything only on the result page.
- Keyboard: `1`-`4` answer, `←`/`→` navigate.

## Files

| File | Purpose |
| --- | --- |
| `app.py` | Flask routes and JSON API |
| `exams.py` | cycle generation, review exams, grading, stats |
| `db.py` | SQLite connection helpers and upserts |
| `schema.sql` | tables: tickets, exams, exam_tickets, attempts, attempt_answers, ticket_stats, meta |
| `scraper.py` | fetch / parse / check / answers commands |
| `seed_demo.py` | synthetic data for UI testing |
| `render_preview.py` | renders templates to static HTML for design review |
| `templates/`, `static/` | UI |

## Notes

- Answers are sent to the browser inside the exam JSON so the practice mode can grade
  instantly. That is fine for a personal, locally-run trainer; if you ever host it publicly,
  strip `correct` from the payload in `app.py` and grade only server-side.
- Georgian text uses `'BPG Arial', 'Noto Sans Georgian', 'Droid Sans Georgian'` with system
  fallbacks; install one of them if the glyphs look wrong.
- Be polite to teoria.on.ge: default scrape delay is 1s/page (47 requests total, one time).
