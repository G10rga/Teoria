# Teoria — B/B1 theory trainer

A Flask app that turns the 921 category-2 (B, B1) tickets from teoria.on.ge into
**31 exams that partition the whole bank** for each signed-in user: every ticket appears
in exactly one exam (30 exams x 30 questions + 1 exam x 21 questions). No repeats, no gaps.
Wrong answers are stored per user and used to build review exams until those tickets
are mastered.

SQLite is the default. PostgreSQL is supported in production via `DATABASE_URL`.

## Setup

```bash
cd Teoria
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set `SECRET_KEY` before you deploy.

### 1. Get the tickets

```bash
python scraper.py scrape --pages 47 --delay 1.0   # download + parse all 47 pages
python scraper.py check                            # report tickets missing a correct answer
```

The scraper caches raw HTML in `html_cache/`, so re-parsing is free:

```bash
python scraper.py parse
python scraper.py inspect --page 1
```

The parser reads each `div.ticket-container`; options are `.t-answer` / `.t-a-num` / `.t-a-text`.
The answer key is the `data-is-correct-list` attribute on the ticket block. `python test_parser.py`
runs offline unit tests over that markup.

Fallback key import:

```bash
python scraper.py answers key.csv   # ticket_id,correct_index  (0-based or 1-based)
```

### 2. Try the UI without scraping

```bash
python seed_demo.py    # 921 synthetic tickets (no user cycle yet)
python app.py          # http://127.0.0.1:5000
```

Register an account. The first dashboard visit generates that user's cycle of 31 exams.

### 3. Run locally

```bash
python app.py          # http://127.0.0.1:5000
```

### 4. Deploy

Set `SECRET_KEY`. For a hosted Postgres database set `DATABASE_URL`. Then:

```bash
gunicorn wsgi:app --bind 0.0.0.0:$PORT
```

A `Procfile` is included for Render / Railway / Heroku-style hosts. After first deploy, either
run `python scraper.py parse` against cached HTML (or scrape once) so the ticket table is filled,
or ship a SQLite file / Postgres dump that already contains `tickets`.

Env vars:

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` / `PRAVA_SECRET` | Flask session signing. Required when `DATABASE_URL` is set. |
| `DATABASE_URL` / `PRAVA_DB` | SQLAlchemy URL or a SQLite file path. Default: `prava.db`. |
| `PRAVA_SECURE_COOKIES` | `1` behind HTTPS so the session cookie is Secure. |
| `PORT` | `app.run` and Procfile bind port. |

An existing single-user `prava.db` is upgraded in place. The first account you register
inherits leftover exam history.

## Users, history, and review exams

- Registration / login is required. Each user has their own exam cycles, attempts, and stats.
- Finished attempts are stored with a pass/fail flag (`passed` when mistakes ≤ 3).
- Every wrong or blank answer is stored in `failed_questions` and counted on `ticket_stats`.
- **შეცდომების გამოცდა** builds a 30-question exam from that user's open mistakes
  (most wrong first). Unseen tickets fill the rest if the pool is short.
- **ისტორია** lists taken exams, with filters for passed and failed sittings.

A ticket is mastered after 1 correct answer if it was never missed, or after 2 consecutive
correct answers if it was missed before. Mastering a ticket resolves its failed-question rows
so they drop out of later review exams.

## How the randomness works

`exams.generate_cycle(user_id)` shuffles that user's copy of the bank once with
`random.Random(seed).shuffle`, then slices it into chunks of 30.
„ახალი ციკლი" reshuffles for that user only.

## Exam rules

- 30 questions, 30-minute countdown, auto-submit at 0.
- 3 or fewer mistakes = pass. Unanswered counts as wrong.
- Question text is in its own box; answers stay on the image cover.
- Exam mode grades on the server and does not send the answer key to the browser.
  Practice mode still reveals the key so it can highlight immediately.
- Keyboard: `1`-`4` answer, `←` / `→` navigate.

## Files

| File | Purpose |
| --- | --- |
| `app.py` | Flask factory, auth, exam routes, JSON API |
| `models.py` | SQLAlchemy models: users, tickets, exams, attempts, stats |
| `exams.py` | cycle generation, review exams, grading, stats |
| `db.py` | engine, schema bootstrap, ticket upserts |
| `config.py` | database URL, cookies, secrets |
| `wsgi.py` | `gunicorn wsgi:app` |
| `schema.sql` | documented table shapes |
| `scraper.py` | fetch / parse / check / answers |
| `seed_demo.py` | synthetic tickets for UI testing |
| `templates/`, `static/` | UI |

## Notes

- Georgian text uses `'BPG Arial', 'Noto Sans Georgian', 'Droid Sans Georgian'` with system
  fallbacks; install one of them if the glyphs look wrong.
- Be polite to teoria.on.ge: default scrape delay is 1s/page (47 requests total, one time).
