"""Gemini explanations for truly-unknown tickets (shared cache on Ticket.ai_explanation)."""
from __future__ import annotations

import os

import requests

from config import Config

# gemini-2.0-flash was shut down 2026-06-01; override with GEMINI_MODEL if needed.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)


def _gemini_error_detail(resp: requests.Response) -> str:
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


class GeminiError(RuntimeError):
    pass


def gemini_configured() -> bool:
    return bool((Config.GEMINI_API_KEY or "").strip())


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


def explain_ticket(ticket) -> str:
    """Return a short Georgian explanation for why the correct option is right."""
    api_key = (Config.GEMINI_API_KEY or "").strip()
    if not api_key:
        raise GeminiError("GEMINI_API_KEY არ არის დაყენებული.")

    answers = ticket.answers if hasattr(ticket, "answers") else []
    correct = ticket.correct_index
    correct_label = None
    if correct is not None and 0 <= correct < len(answers):
        correct_label = f"{correct + 1}. {answers[correct]}"

    options_block = "\n".join(f"{i + 1}. {text}" for i, text in enumerate(answers)) or "(პასუხები არ არის)"
    official = (ticket.explanation or "").strip()

    wrong_nums = [
        str(i + 1) for i in range(len(answers))
        if correct is None or i != correct
    ]
    wrong_hint = ", ".join(wrong_nums) if wrong_nums else "—"

    prompt = f"""მოკლე სასწავლო ახსნა B/B1 ბილეთისთვის. მკითხველი დამწყებია.

დაწერე მხოლოდ ქართულად, ამ ზუსტი სტრუქტურით (სხვა ტექსტი, მისალმება და შესავალი არ დაწერო):

სწორი პასუხი: {correct_label or "უცნობი"}

რატომ სწორია:
- ...
- ...

რატომ არა სხვები:
- ...

წესები:
- სულ მაქს. 100 სიტყვა; მოკლე და პირდაპირი.
- „რატომ სწორია“ — 1–2 მოკლე პუნქტი; „რატომ არა სხვები“ — თითო არასწორი ვარიანტისთვის ერთი მოკლე ხაზი (ვარიანტები: {wrong_hint}).
- აკრძალულია: „გამარჯობა“, „მე ვარ მასწავლებელი“, კითხვის გამეორება, ზედმეტი კონტექსტი, გრძელი ანალიზი, კანონის მუხლების გამოგონება.
- მასწავლებლის ტონი არ გამოიყენო — მხოლოდ ფაქტები.
- თუ სურათი/ნიშანია, მხოლოდ 1 წინადადებით მიუთითე რა უნდა დაინახო.
- თუ ოფიციალური განმარტება არის — მის მიხედვით, მაგრამ უფრო მარტივად.

ბილეთი #{ticket.id}
კითხვა: {ticket.question}

ვარიანტები:
{options_block}

ოფიციალური განმარტება:
{official or "(არ არის)"}
"""

    try:
        resp = requests.post(
            GEMINI_URL,
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 512,
                },
            },
            timeout=60,
        )
    except requests.RequestException as exc:
        raise GeminiError(f"Gemini-სთან კავშირი ვერ დამყარდა: {exc}") from exc
    if resp.status_code >= 400:
        detail = _gemini_error_detail(resp)
        raise GeminiError(f"Gemini შეცდომა ({resp.status_code}): {detail}")

    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise GeminiError(f"Gemini-მ პასუხი დაბლოკა ({block}).")
        raise GeminiError("Gemini-მ ცარიელი ან მოულოდნელი პასუხი დააბრუნა.")
    try:
        parts = candidates[0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiError("Gemini-მ ცარიელი ან მოულოდნელი პასუხი დააბრუნა.") from exc
    if not text:
        raise GeminiError("Gemini-მ ცარიელი ტექსტი დააბრუნა.")
    return _normalize_explanation(text)
