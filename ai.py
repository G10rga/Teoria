"""AI explanations for truly-unknown tickets (shared cache on Ticket.ai_explanation).

Providers (pick via env):
  - gemini — GEMINI_API_KEY / GOOGLE_API_KEY (preferred: better Georgian)
  - groq   — GROQ_API_KEY (fast fallback)
"""
from __future__ import annotations

import os

import requests

from config import Config

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# llama-3.3-70b-versatile was shut down 2026-08-16 on free/developer tiers.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# gemini-2.0-flash was shut down 2026-06-01. Keep thinkingBudget=0 for speed.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)


class AIError(RuntimeError):
    pass


# Back-compat alias used by app.py
GeminiError = AIError


def _provider() -> str:
    explicit = (Config.AI_PROVIDER or os.environ.get("AI_PROVIDER") or "").strip().lower()
    if explicit in ("groq", "gemini"):
        return explicit
    # Prefer Gemini for Georgian quality when both keys exist.
    if (Config.GEMINI_API_KEY or "").strip():
        return "gemini"
    if (Config.GROQ_API_KEY or "").strip():
        return "groq"
    return ""


def ai_provider_name() -> str | None:
    return _provider() or None


def ai_configured() -> bool:
    return bool(_provider())


# Back-compat for templates / routes
def gemini_configured() -> bool:
    return ai_configured()


def _api_error_detail(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return (resp.text or resp.reason or "unknown error")[:300]
    err = payload.get("error")
    if isinstance(err, dict):
        return (err.get("message") or err.get("status") or str(err))[:300]
    if isinstance(err, str):
        return err[:300]
    return (resp.text or resp.reason or "unknown error")[:300]


def _normalize_explanation(text: str) -> str:
    """Drop common filler openings models add despite instructions."""
    lines = [ln.rstrip() for ln in text.strip().splitlines()]
    while lines:
        head = lines[0].strip().lower()
        if not head:
            lines.pop(0)
            continue
        if head.startswith(("გამარჯობა", "hello", "hi ", "hey ")):
            lines.pop(0)
            continue
        if "მასწავლებელი" in head and head.startswith(("მე ", "შენ ", "გამ")):
            lines.pop(0)
            continue
        if head.startswith(("მოდით", "დღეს ", "ეს კითხვა")):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


def _looks_incomplete(text: str, wrong_count: int) -> bool:
    stripped = text.strip()
    if len(stripped) < 80:
        return True
    if "რატომ სწორია" not in stripped:
        return True
    if wrong_count and "რატომ არა" not in stripped:
        return True
    last = stripped.split()[-1].lower().rstrip(".,;:!?»\"")
    if last in {"და", "რომ", "ან", "თუ", "რაც", "ვინ", "როდესაც", "რადგან"}:
        return True
    return False


def _candidate_text(candidate: dict) -> str:
    """Join visible answer text; skip Gemini thought parts."""
    content = candidate.get("content") or {}
    parts = content.get("parts") or []
    chunks = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("thought"):
            continue
        piece = (part.get("text") or "").strip()
        if piece:
            chunks.append(piece)
    return "\n".join(chunks).strip()


def _build_prompt(ticket) -> tuple[str, int]:
    answers = ticket.answers if hasattr(ticket, "answers") else []
    correct = ticket.correct_index
    correct_num = str(correct + 1) if correct is not None else "?"
    options_block = "\n".join(f"{i + 1}. {text}" for i, text in enumerate(answers)) or "(პასუხები არ არის)"
    official = (ticket.explanation or "").strip()
    wrong_nums = [
        str(i + 1) for i in range(len(answers))
        if correct is None or i != correct
    ]
    wrong_hint = ", ".join(wrong_nums) if wrong_nums else "—"

    prompt = f"""სასწავლო ახსნა B/B1 ბილეთისთვის. მკითხველი დამწყებია — მოკლედ, მაგრამ სრულად.

დაწერე მხოლოდ ქართულად (მხოლოდ ქართული ანბანი), ამ სტრუქტურით.
ყველა ნაწილი დაასრულე; წინადადება ნახევრად არ დატოვო.
ინგლისური სიტყვები, ლათინური ასოები და ტრანსლიტერაცია აკრძალულია (მაგ. manoeuvre, priority, bus — არა).

სწორი პასუხი: ვარიანტი {correct_num}

რატომ სწორია:
- (2–3 მოკლე პუნქტი; თითო ერთი ან ორი წინადადება)
- ...
- ...

რატომ არა სხვები:
- (თითო არასწორი ვარიანტისთვის ერთი მოკლე ხაზი: „2: ...“, „3: ...“ — ვარიანტები: {wrong_hint})

წესები:
- დაახლ. 120–180 სიტყვა: სრული ახსნა, მაგრამ არა გრძელი ესე.
- აკრძალულია: მისალმება, „მე ვარ მასწავლებელი“, კითხვის გამეორება, კანონის მუხლების გამოგონება, ზედმეტი ისტორია.
- „სწორი პასუხი“ ხაზზე მხოლოდ ნომერი; ნუ გაიმეორებ მთელ პასუხის ტექსტს.
- თუ ოფიციალური განმარტება არის — მის მიხედვით, მარტივი ენით.
- თუ სურათი/ნიშანია — 1 მოკლე წინადადებით.

ბილეთი #{ticket.id}
კითხვა: {ticket.question}

ვარიანტები:
{options_block}

ოფიციალური განმარტება:
{official or "(არ არის)"}
"""
    return prompt, len(wrong_nums)


def _call_groq(prompt: str, api_key: str) -> str:
    try:
        resp = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "temperature": 0.25,
                "max_tokens": 900,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Write short driving-theory explanations in Georgian only "
                            "(Mkhedruli script). Never use English words or Latin letters. "
                            "Follow the user's structure exactly. No greetings."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=25,
        )
    except requests.RequestException as exc:
        raise AIError(f"Groq-თან კავშირი ვერ დამყარდა: {exc}") from exc
    if resp.status_code == 429:
        raise AIError("Groq-ის ლიმიტი ამოიწურა (429). დაელოდეთ 1 წუთი და სცადეთ თავიდან.")
    if resp.status_code >= 400:
        raise AIError(f"Groq შეცდომა ({resp.status_code}): {_api_error_detail(resp)}")

    data = resp.json()
    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
        finish = data["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError("Groq-მ ცარიელი ან მოულოდნელი პასუხი დააბრუნა.") from exc
    if finish == "length" and len(text) < 80:
        raise AIError("AI ახსნა უსრულო დარჩა (ტოკენების ლიმიტი). სცადეთ თავიდან.")
    if not text:
        raise AIError("Groq-მ ცარიელი ტექსტი დააბრუნა.")
    return text


def _post_gemini(api_key: str, body: dict, *, timeout: int = 35) -> requests.Response:
    return requests.post(
        GEMINI_URL,
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )


def _call_gemini(prompt: str, api_key: str) -> str:
    # thinkingBudget=0: fast answers, avoid Cloudflare timeouts / empty outputs.
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 1200,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    try:
        resp = _post_gemini(api_key, body)
    except requests.RequestException as exc:
        raise AIError(f"Gemini-სთან კავშირი ვერ დამყარდა: {exc}") from exc

    if resp.status_code == 429:
        raise AIError("Gemini-ის ლიმიტი ამოიწურა (429). დაელოდეთ 1 წუთი და სცადეთ თავიდან.")

    if resp.status_code >= 400:
        # Older/newer models may reject thinkingConfig — retry without it.
        detail = _api_error_detail(resp)
        if resp.status_code == 400 and "thinking" in (resp.text or "").lower():
            body["generationConfig"].pop("thinkingConfig", None)
            try:
                resp = _post_gemini(api_key, body)
            except requests.RequestException as exc:
                raise AIError(f"Gemini-სთან კავშირი ვერ დამყარდა: {exc}") from exc
            if resp.status_code >= 400:
                raise AIError(f"Gemini შეცდომა ({resp.status_code}): {_api_error_detail(resp)}")
        else:
            raise AIError(f"Gemini შეცდომა ({resp.status_code}): {detail}")

    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise AIError(f"Gemini-მ პასუხი დაბლოკა ({block}).")
        raise AIError("Gemini-მ ცარიელი ან მოულოდნელი პასუხი დააბრუნა.")

    candidate = candidates[0]
    text = _candidate_text(candidate)
    finish = candidate.get("finishReason")
    if finish == "MAX_TOKENS" and len(text) < 80:
        raise AIError("AI ახსნა უსრულო დარჩა (ტოკენების ლიმიტი). სცადეთ თავიდან.")
    if not text:
        raise AIError("Gemini-მ ცარიელი ტექსტი დააბრუნა.")
    return text


def explain_ticket(ticket) -> str:
    """Return a concise but complete Georgian explanation for the correct option."""
    provider = _provider()
    if not provider:
        raise AIError("AI API გასაღები არ არის დაყენებული (GEMINI_API_KEY ან GROQ_API_KEY).")

    prompt, wrong_count = _build_prompt(ticket)
    if provider == "groq":
        raw = _call_groq(prompt, (Config.GROQ_API_KEY or "").strip())
    else:
        raw = _call_gemini(prompt, (Config.GEMINI_API_KEY or "").strip())

    text = _normalize_explanation(raw)
    if _looks_incomplete(text, wrong_count):
        raise AIError("AI ახსნა უსრულო დარჩა. დააჭირეთ „ახსნის განახლება“.")
    return text
