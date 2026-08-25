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


def _call_gemini(prompt: str, api_key: str, *, max_output_tokens: int) -> tuple[str, str | None]:
    try:
        resp = requests.post(
            GEMINI_URL,
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.25,
                    "maxOutputTokens": max_output_tokens,
                },
            },
            timeout=45,
        )
    except requests.RequestException as exc:
        raise GeminiError(f"Gemini-სთან კავშირი ვერ დამყარდა: {exc}") from exc
    if resp.status_code == 429:
        raise GeminiError("Gemini-ის ლიმიტი ამოიწურა (429). დაელოდეთ 1 წუთი და სცადეთ თავიდან.")
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
        finish = candidates[0].get("finishReason")
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiError("Gemini-მ ცარიელი ან მოულოდნელი პასუხი დააბრუნა.") from exc
    if finish == "MAX_TOKENS" and len(text) < 80:
        raise GeminiError("AI ახსნა უსრულო დარჩა (ტოკენების ლიმიტი). სცადეთ თავიდან.")
    if not text:
        raise GeminiError("Gemini-მ ცარიელი ტექსტი დააბრუნა.")
    return text, finish


def explain_ticket(ticket) -> str:
    """Return a concise but complete Georgian explanation for the correct option."""
    api_key = (Config.GEMINI_API_KEY or "").strip()
    if not api_key:
        raise GeminiError("GEMINI_API_KEY არ არის დაყენებული.")

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

დაწერე მხოლოდ ქართულად, ამ სტრუქტურით (ყველა ნაწილი დაასრულე, წინადადება ნახევრად არ დატოვო):

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

    wrong_count = len(wrong_nums)
    raw, finish = _call_gemini(prompt, api_key, max_output_tokens=1536)
    text = _normalize_explanation(raw)
    if finish == "MAX_TOKENS" and not _looks_incomplete(text, wrong_count):
        return text
    if _looks_incomplete(text, wrong_count):
        raise GeminiError("AI ახსნა უსრულო დარჩა. დააჭირეთ „ახსნის განახლება“.")
    return text
