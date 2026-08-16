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

### Tickets

Images live in `static/tickets/`. Question text is stored in the database. Import once:

```bash
python scraper.py scrape --pages 47 --delay 1.0
```

Register an account. The first dashboard visit generates that user's cycle of 31 exams.

### Run locally

```bash
python app.py          # http://127.0.0.1:5000
```

### Deploy (Ubuntu + PostgreSQL + Cloudflare Tunnel)

Nginx is not used. Cloudflare Tunnel publishes `teoria.g1orga.dev` to gunicorn on
**127.0.0.1:8012** (8000/8001 stay free for your other tunnels). PostgreSQL listens on
localhost only.

On the server:

```bash
git clone https://github.com/G10rga/Teoria.git /opt/teoria
cd /opt/teoria
sudo ./deploy/setup-ubuntu.sh
```

Then add this hostname to `/etc/cloudflared/config.yml` without removing your
other rules (see `deploy/cloudflared-ingress.snippet.yml`):

```yaml
  - hostname: teoria.g1orga.dev
    service: http://127.0.0.1:8012
```

```bash
sudo cloudflared tunnel route dns <TUNNEL_NAME> teoria.g1orga.dev
sudo systemctl restart cloudflared
curl -sS http://127.0.0.1:8012/health
```

Import tickets once (images land in `static/tickets/`):

```bash
sudo -u teoria bash -lc 'cd /opt/teoria && set -a && . ./.env && set +a && .venv/bin/python scraper.py scrape --pages 47 --delay 1.0'
```

If 8012 is also taken: `sudo TEORIA_PORT=8013 ./deploy/setup-ubuntu.sh` and point the
tunnel at that port.

A `Procfile` is still included for Render / Railway / Heroku-style hosts.

Env vars:

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Flask session signing. Required in production. |
| `DATABASE_URL` | SQLAlchemy URL. Default local file is `teoria.db` (not in git). |
| `TEORIA_SECURE_COOKIES` | `1` behind HTTPS (Cloudflare) so the session cookie is Secure. |
| `TEORIA_PORT` | Loopback port for gunicorn. Default `8012`. |
| `PREFERRED_URL_SCHEME` | `https` when the public URL is HTTPS. |
| `PORT` | `app.run` and Procfile bind port (local / PaaS). |

An existing local `prava.db` is still opened if `teoria.db` is missing.

## Users, history, and review exams

- Registration / login is required. Each user has their own exam cycles, attempts, and stats.
- Finished attempts are stored with a pass/fail flag (`passed` when mistakes ≤ 5).
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
- 5 or fewer mistakes = pass. Unanswered counts as wrong.
- Question text is in its own box; answers stay on the image cover.
- Exam mode grades on the server and does not send the answer key to the browser.
  Practice mode still reveals the key so it can highlight immediately.
- Keyboard: `1`-`4` answer, `←` / `→` navigate.

## Notes

- Georgian text uses `'BPG Arial', 'Noto Sans Georgian', 'Droid Sans Georgian'` with system
  fallbacks; install one of them if the glyphs look wrong.
- Be polite to teoria.on.ge: default scrape delay is 1s/page (47 requests total, one time).
