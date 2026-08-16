"""Scraper for teoria.on.ge ticket bank (default: category 2 = B, B1).

Usage
-----
    python scraper.py fetch                 # download all 47 pages of HTML into html_cache/
    python scraper.py parse                 # parse html_cache/ into teoria.db (no network)
    python scraper.py scrape                # fetch + parse in one go
    python scraper.py inspect --page 1      # dump structure of one ticket block (debug)
    python scraper.py check                            # report tickets missing a correct answer
    python scraper.py answers key.csv                  # import an answer key: ticket_id,correct_index
    python scraper.py repair                           # re-fetch tickets with no key or dropped one-letter options (e.g. "I")
    python scraper.py repair --ticket 653              # refresh one ticket

Why the two-step design: HTML is cached on disk, so re-parsing after a selector
fix costs zero extra requests to the site.

If `check` reports missing correct answers, run `inspect` and adjust
CORRECT_ATTRS / CORRECT_CLASS_HINTS below - that is the only site-specific part.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

import db

BASE = "https://teoria.on.ge"
LIST_URL = BASE + "/tickets/{category}?page={page}"
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "html_cache"
IMAGE_DIR = BASE_DIR / "static" / "tickets"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ka,en;q=0.8",
}

# --- site markup (matched against the real cat-2 pages) ----------------------
TICKET_LINK_RE = re.compile(r"tickets\?ticket=(\d+)")
CONTAINER_SELECTOR = "article.ticket-container, div.ticket-container"
ANSWER_SELECTOR = ".t-answer"
ANSWER_TEXT_SELECTOR = ".t-a-text"
ANSWER_NUM_SELECTOR = ".t-a-num"
EMPTY_ANSWER_CLASS = "ans-empty"     # unused answer slots on 2- and 3-option tickets
QUESTION_SELECTORS = (".t-question-inner", ".t-question", ".t-q-text")
EXPLANATION_SELECTORS = (".desc-box-inner", ".desc-box", ".t-explanation")
EXPLANATION_DROP = ".desc-title, .ticket-link, .ticket-page-link, input"
IMAGE_SELECTORS = ("figure.t-image img", ".t-image img")
NUM_SELECTOR = ".t-num"
COVER_SELECTOR = ".t-cover"
# Layout classes that drive the site's answers-over-image overlay:
#   cutoff-N               answer cover height (1 = short, 3 = tall)
#   answers-num-N          how many answer slots are visible
#   big-answers            larger answer text
#   ticket-container-small 750x512 base instead of 940x631
LAYOUT_PREFIXES = ("cutoff-", "answers-num-", "big-answers",
                   "ticket-container-small")
ANSWER_ORDER_RE = re.compile(r"t-answer-(\d+)$")
# the answer key lives in one attribute on each ticket block
CORRECT_LIST_ATTR = "data-is-correct-list"
CORRECT_ITEM_ATTRS = ("data-is-correct", "data-correct", "data-answer", "data-true")
TRUE_TOKENS = {"1", "true", "yes", "y", "t", "correct", "on"}
FALSE_TOKENS = {"0", "false", "no", "n", "f", "wrong", "off", "null", "none", ""}
JUNK_SELECTOR = "script, style, .admixer-ad, .goto-ticket, .on-pagination, .paginator"
IMAGE_SKIP = ("admixer", "banner", "logo", "sprite", "icon", "avatar", "pixel", "/ads")
EXPLANATION_MARKER = "ბილეთის განმარტება"
ANSWER_LINE_RE = re.compile(r"^\s*([1-4])[\.\)\s]\s*(.+)$")
MAX_ANSWERS = 8          # a real ticket never has more; more means blocks merged
# -----------------------------------------------------------------------------


# ------------------------------------------------------------------ fetching
def fetch_pages(category: int, pages: int, delay: float, force: bool) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)
    for page in range(1, pages + 1):
        target = CACHE_DIR / f"cat{category}_p{page:02d}.html"
        if target.exists() and not force:
            print(f"  skip page {page} (cached)")
            continue
        url = LIST_URL.format(category=category, page=page)
        for attempt in range(4):
            try:
                resp = session.get(url, timeout=30)
                resp.raise_for_status()
                resp.encoding = resp.encoding or "utf-8"
                target.write_text(resp.text, encoding="utf-8")
                print(f"  page {page}/{pages} -> {len(resp.text):,} bytes")
                break
            except Exception as exc:                      # noqa: BLE001
                wait = 2 ** attempt
                print(f"  page {page} failed ({exc}); retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
        else:
            print(f"  GIVING UP on page {page}", file=sys.stderr)
        time.sleep(delay)


# ------------------------------------------------------------------- parsing
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _item_is_correct(node: Tag) -> bool:
    """True if a single .t-answer node is flagged as the correct one.

    The live site marks the winning option with data-is-correct-list="true" on
    the <p class="t-answer"> itself - despite the name it is a per-answer flag,
    not a list. The list form is still handled by _parse_correct_list for
    blocks that carry one combined attribute.
    """
    for attr in (CORRECT_LIST_ATTR, *CORRECT_ITEM_ATTRS):
        if str(node.get(attr, "")).strip().lower() in TRUE_TOKENS - {"on"}:
            return True
    classes = " ".join(node.get("class") or []).lower()
    return "correct" in classes and "in-correct" not in classes and "incorrect" not in classes


def _correct_list_value(container: Tag) -> str | None:
    """Find a combined answer-key attribute on, inside, or above the block.

    Per-answer flags are handled by _item_is_correct; a node that is itself an
    answer is skipped here so a single "true" is never mistaken for a list.
    """
    if container.has_attr(CORRECT_LIST_ATTR) and not _is_answer_node(container):
        return container[CORRECT_LIST_ATTR]
    for node in container.find_all(attrs={CORRECT_LIST_ATTR: True}):
        if not _is_answer_node(node):
            return node[CORRECT_LIST_ATTR]
    for parent in container.parents:
        if isinstance(parent, Tag) and parent.has_attr(CORRECT_LIST_ATTR):
            return parent[CORRECT_LIST_ATTR]
    return None


def _parse_correct_list(raw, count: int) -> int | None:
    """Turn a data-is-correct-list value into a 0-based index.

    Handles "0,0,1,0", "false,true", "[0,1,0]", "no|yes|no" and a bare index.
    """
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        tokens = [str(t).strip().lower() for t in raw]
    else:
        text = str(raw).strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except Exception:                                  # noqa: BLE001
                parsed = None
            if isinstance(parsed, list):
                return _parse_correct_list(parsed, count)
        text = text.strip("[]() ")
        tokens = [t.strip().strip("\"'").lower()
                  for t in re.split(r"[,;|\s]+", text) if t.strip()]
    if not tokens:
        return None

    flags = []
    for token in tokens:
        if token in TRUE_TOKENS:
            flags.append(True)
        elif token in FALSE_TOKENS:
            flags.append(False)
        else:
            flags.append(None)

    # one flag per answer -> index of the true one
    if len(tokens) > 1 and all(flag is not None for flag in flags):
        return flags.index(True) if True in flags else None

    # a bare number -> the answer's own 1-based number (0 means "first")
    if len(tokens) == 1 and tokens[0].isdigit():
        value = int(tokens[0])
        if value == 0:
            return 0
        return value - 1 if 1 <= value <= max(count, 1) else None
    return None


def _ticket_container(link: Tag) -> Tag:
    """Climb from the '/tickets?ticket=N' link to the block holding the question."""
    node = link
    best = link
    for _ in range(8):
        node = node.parent
        if not isinstance(node, Tag) or node.name in ("body", "html"):
            break
        best = node
        text = node.get_text(" ", strip=True)
        # a full ticket block has the question mark and at least two options
        if len(text) > 120 and len(TICKET_LINK_RE.findall(str(node))) == 1:
            if node.find("img") or "?" in text or ":" in text:
                return node
    return best


def _is_answer_node(node: Tag) -> bool:
    return "t-answer" in (node.get("class") or [])


def _answer_nodes(container: Tag) -> list[Tag]:
    """Outer .t-answer blocks, ordered by their t-answer-N class.

    Slots marked `ans-empty` are placeholders on tickets with fewer than four
    options and are dropped, so a 2-option ticket yields exactly 2 answers.
    """
    nodes = [n for n in container.select(ANSWER_SELECTOR)
             if EMPTY_ANSWER_CLASS not in (n.get("class") or [])]
    node_set = set(id(n) for n in nodes)
    outer = [n for n in nodes
             if not any(id(p) in node_set for p in n.parents)]

    def order(node: Tag) -> int:
        for cls in node.get("class") or []:
            match = ANSWER_ORDER_RE.search(cls)
            if match:
                return int(match.group(1))
        return 99

    return sorted(outer, key=order)


def _answer_text(node: Tag) -> str:
    text_node = node.select_one(ANSWER_TEXT_SELECTOR)
    if text_node is not None:
        return _clean(text_node.get_text(" ", strip=True))
    text = _clean(node.get_text(" ", strip=True))
    num_node = node.select_one(ANSWER_NUM_SELECTOR)
    if num_node is not None:
        num = _clean(num_node.get_text())
        if num and text.startswith(num):
            text = _clean(text[len(num):])
    match = ANSWER_LINE_RE.match(text)
    return match.group(2) if match else text


def _extract_answers(container: Tag) -> tuple[list[str], int | None]:
    texts, correct = [], None
    for node in _answer_nodes(container):
        text = _answer_text(node)
        # keep one-character answers such as Roman "I"; empty slots are already
        # filtered via ans-empty
        if not text:
            continue
        if _item_is_correct(node):
            correct = len(texts)
        texts.append(text)

    if correct is None:
        correct = _parse_correct_list(_correct_list_value(container), len(texts))
    if correct is not None and not (0 <= correct < len(texts)):
        correct = None
    if len(texts) >= 2:
        return texts, correct

    # fallback: numbered lines in raw text
    texts = []
    for raw in container.get_text("\n", strip=True).split("\n"):
        match = ANSWER_LINE_RE.match(_clean(raw))
        if match:
            texts.append(match.group(2))
    return texts, _parse_correct_list(_correct_list_value(container), len(texts))


def _extract_question(container: Tag, answers: list[str]) -> str:
    """Question text: the dedicated element if present, else block text."""
    for selector in QUESTION_SELECTORS:
        node = container.select_one(selector)
        if node is not None:
            text = _clean(node.get_text(" ", strip=True))
            if len(text) >= 10:
                return text

    clone = BeautifulSoup(str(container), "lxml")
    for node in clone.select(ANSWER_SELECTOR):
        node.decompose()
    for node in clone.select(JUNK_SELECTOR):
        node.decompose()

    fallback = None
    for raw in clone.get_text("\n", strip=True).split("\n"):
        text = _clean(raw)
        if len(text) < 15 or text.startswith("#"):
            continue
        if text in answers or ANSWER_LINE_RE.match(text):
            continue
        if EXPLANATION_MARKER in text:
            break
        if text.endswith("?"):
            return text
        fallback = fallback or text
    return fallback or _clean(container.get_text())[:300]


def _extract_explanation(container: Tag) -> str | None:
    for selector in EXPLANATION_SELECTORS:
        node = container.select_one(selector)
        if node is None:
            continue
        clone = BeautifulSoup(str(node), "lxml")
        for junk in clone.select(EXPLANATION_DROP):
            junk.decompose()
        text = _clean(clone.get_text(" ", strip=True))
        text = text.split("ბმული ბილეთზე", 1)[0]
        text = re.sub(rf"^{EXPLANATION_MARKER}[:\s]*", "", text).strip()
        if len(text) > 20:
            return _clean(text)

    text = container.get_text("\n", strip=True)
    marker = "ბილეთის განმარტება"
    if marker in text:
        tail = text.split(marker, 1)[1]
        tail = tail.split("ბმული ბილეთზე", 1)[0]
        tail = re.sub(r"^[:\s]*(ბილეთის გვერ���ი)?", "", tail).strip()
        return _clean(tail) or None
    return None


def _ticket_id(container: Tag) -> int | None:
    num_node = container.select_one(NUM_SELECTOR)
    if num_node is not None:
        match = re.search(r"(\d+)", num_node.get_text())
        if match:
            return int(match.group(1))
    link = container.find("a", href=TICKET_LINK_RE)
    if link is not None:
        return int(TICKET_LINK_RE.search(link["href"]).group(1))
    for attr in ("data-ticket-id", "data-ticket", "data-id", "id"):
        match = re.search(r"(\d+)", str(container.get(attr, "")))
        if match:
            return int(match.group(1))
    match = re.search(r"#\s?(\d+)", container.get_text(" ", strip=True))
    return int(match.group(1)) if match else None


def _layout(container: Tag) -> str | None:
    """Site layout classes for this ticket, used to place answers on the image."""
    node = container
    if "ticket-container" not in (container.get("class") or []):
        node = container.select_one(CONTAINER_SELECTOR) or container
    found = set(node.get("class") or [])
    cover = node.select_one(COVER_SELECTOR)
    if cover is not None:
        found |= set(cover.get("class") or [])
    keep = sorted(c for c in found if c.startswith(LAYOUT_PREFIXES))
    return " ".join(keep) or None


def _ticket_image(container: Tag) -> str | None:
    for selector in IMAGE_SELECTORS:
        node = container.select_one(selector)
        src = (node.get("src") or node.get("data-src") or "") if node else ""
        if src and not any(bad in src.lower() for bad in IMAGE_SKIP):
            return urljoin(BASE, src)
    for img in container.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src or any(bad in src.lower() for bad in IMAGE_SKIP):
            continue
        return urljoin(BASE, src)
    return None


def _ticket_ids_in(node: Tag) -> set[int]:
    return {int(TICKET_LINK_RE.search(a["href"]).group(1))
            for a in node.find_all("a", href=TICKET_LINK_RE)}


def _grow_block(anchor: Tag) -> Tag:
    """Climb from an anchor to the largest ancestor that still holds ONE ticket.

    Stops before any ancestor that would pull in a second ticket (a second
    answer-key attribute or a second ticket id), so neighbouring tickets can
    never be merged into one block.
    """
    current = best = anchor
    for _ in range(12):
        parent = current.parent
        if not isinstance(parent, Tag) or parent.name in ("body", "html", "[document]"):
            break
        if len(parent.select(f"[{CORRECT_LIST_ATTR}]")) > 1:
            break
        if len(_ticket_ids_in(parent)) > 1:
            break
        current = best = parent
    return best


def ticket_blocks(soup: BeautifulSoup) -> list[Tag]:
    """One block per ticket, grown from whichever anchor the page provides.

    Anchor priority: the answer-key attribute (one per ticket), then answer
    nodes, then ticket links. A class-based wrapper is never trusted, because
    the list wrapper reuses the same class as the ticket blocks.
    """
    anchors = (soup.select(f"[{CORRECT_LIST_ATTR}]")
               or soup.select(ANSWER_SELECTOR)
               or soup.find_all("a", href=TICKET_LINK_RE))
    blocks, seen = [], set()
    for anchor in anchors:
        block = _grow_block(anchor)
        if id(block) in seen:
            continue
        seen.add(id(block))
        blocks.append(block)
    return blocks


def parse_html(html: str, category_label: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    tickets: list[dict] = []
    seen: set[int] = set()

    for container in ticket_blocks(soup):
        ticket_id = _ticket_id(container)
        if ticket_id is None or ticket_id in seen:
            continue
        answers, correct = _extract_answers(container)
        if not 2 <= len(answers) <= MAX_ANSWERS:
            print(f"  ! ticket {ticket_id}: {len(answers)} answers, skipped",
                  file=sys.stderr)
            continue
        seen.add(ticket_id)
        tickets.append({
            "id": ticket_id,
            "category": category_label,
            "question": _extract_question(container, answers),
            "answers": answers,
            "correct_index": correct,
            "image_url": _ticket_image(container),
            "layout": _layout(container),
            "explanation": _extract_explanation(container),
            "source_url": f"{BASE}/tickets?ticket={ticket_id}",
        })
    return tickets


def download_image(url: str, ticket_id: int, session: requests.Session) -> str | None:
    if not url:
        return None
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(urlparse(url).path).suffix or ".jpg"
    path = IMAGE_DIR / f"{ticket_id}{ext}"
    rel = f"tickets/{path.name}"
    if path.exists():
        return rel
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        path.write_bytes(resp.content)
        return rel
    except Exception as exc:                              # noqa: BLE001
        print(f"  image for #{ticket_id} failed: {exc}", file=sys.stderr)
        return None


def parse_cache(category: int, category_label: str, with_images: bool) -> None:
    files = sorted(CACHE_DIR.glob(f"cat{category}_p*.html"))
    if not files:
        sys.exit("html_cache/ is empty - run `python scraper.py fetch` first")

    db.init_db()
    http = requests.Session()
    http.headers.update(HEADERS)
    total, missing_key = 0, 0
    with db.connect() as session:
        for file in files:
            tickets = parse_html(file.read_text(encoding="utf-8"), category_label)
            for ticket in tickets:
                image_url = ticket.pop("image_url", None)
                ticket["image"] = (download_image(image_url, ticket["id"], http)
                                   if with_images else None)
                if ticket["correct_index"] is None:
                    missing_key += 1
                db.upsert_ticket(ticket, session=session)
                total += 1
            print(f"  {file.name}: {len(tickets)} tickets")
    print(f"\nimported {total} tickets; {missing_key} without a correct answer")
    if missing_key:
        print("run `python scraper.py inspect --page 1` and check the"
              f" {CORRECT_LIST_ATTR} attribute format")


# ------------------------------------------------------------------ commands
def cmd_inspect(category: int, page: int) -> None:
    file = CACHE_DIR / f"cat{category}_p{page:02d}.html"
    if not file.exists():
        sys.exit(f"{file} not found - run fetch first")
    soup = BeautifulSoup(file.read_text(encoding="utf-8"), "lxml")
    blocks = ticket_blocks(soup)
    print(f"{len(blocks)} ticket blocks on page {page}")
    if not blocks:
        sys.exit("no ticket blocks found - the markup changed")

    container = blocks[0]
    snippet_path = BASE_DIR / "inspect_block.html"
    snippet_path.write_text(container.prettify(), encoding="utf-8")
    print(f"full block saved to {snippet_path}\n")

    raw_key = _correct_list_value(container)
    answers, correct = _extract_answers(container)
    print(f"id:               {_ticket_id(container)}")
    print(f"{CORRECT_LIST_ATTR}: {raw_key!r}")
    print(f"parsed correct:   {correct}")
    print(f"question:         {_extract_question(container, answers)}")
    print(f"image:            {_ticket_image(container)}")
    print(f"answers:          {json.dumps(answers, ensure_ascii=False, indent=2)}")
    print(f"explanation:      {_extract_explanation(container)}")

    keys = [_correct_list_value(b) for b in blocks]
    print(f"\nanswer-key attribute present on {sum(k is not None for k in keys)}"
          f"/{len(blocks)} blocks; distinct sample: {sorted({str(k) for k in keys})[:6]}")


def cmd_check() -> None:
    from models import Ticket
    db.init_db()
    with db.connect() as session:
        total = session.query(Ticket).count()
        missing = [t.id for t in session.query(Ticket).filter(Ticket.correct_index.is_(None)).order_by(Ticket.id)]
        no_img = session.query(Ticket).filter(Ticket.image.is_(None)).count()
    print(f"tickets: {total}")
    print(f"without correct answer: {len(missing)}")
    print(f"without image: {no_img}")
    if missing:
        print("first missing ids:", ", ".join(str(i) for i in missing[:40]))


def cmd_answers(path: str) -> None:
    """Import a CSV answer key (ticket_id,correct_index) - 0-based or 1-based."""
    from models import Ticket
    db.init_db()
    updated = 0
    with db.connect() as session, open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) < 2 or not row[0].strip().isdigit():
                continue
            ticket_id, raw = int(row[0]), int(row[1])
            ticket = session.get(Ticket, ticket_id)
            if not ticket:
                continue
            n = len(ticket.answers)
            ticket.correct_index = raw - 1 if raw >= n else raw
            updated += 1
    print(f"updated {updated} tickets")


def _looks_like_dropped_short_answer(answers: list) -> bool:
    """True if Roman numeral I was likely discarded (len < 2 filter)."""
    labels = [str(a).strip() for a in answers]
    if "I" in labels:
        return False
    return any(a in labels for a in ("II", "III", "IV", "VI", "VII", "VIII", "IX"))


def refresh_tickets(ids: list[int], category_label: str, with_images: bool = True) -> int:
    """Re-fetch individual ticket pages and upsert them."""
    from models import Ticket
    db.init_db()
    http = requests.Session()
    http.headers.update(HEADERS)
    updated = 0
    with db.connect() as session:
        for tid in ids:
            url = f"{BASE}/tickets?ticket={tid}"
            try:
                resp = http.get(url, timeout=30)
                resp.raise_for_status()
            except Exception as exc:                              # noqa: BLE001
                print(f"  #{tid} fetch failed: {exc}", file=sys.stderr)
                continue
            parsed = parse_html(resp.text, category_label)
            hit = next((t for t in parsed if t["id"] == tid), None)
            if hit is None and parsed:
                hit = parsed[0]
            if hit is None:
                print(f"  #{tid} not found in page", file=sys.stderr)
                continue
            existing = session.get(Ticket, tid)
            image_url = hit.pop("image_url", None)
            image = download_image(image_url, hit["id"], http) if with_images else None
            if not image and existing is not None:
                image = existing.image
            hit["image"] = image
            db.upsert_ticket(hit, session=session)
            updated += 1
            print(f"  #{hit['id']}: {len(hit['answers'])} answers, correct={hit['correct_index']}")
            time.sleep(0.4)
    print(f"refreshed {updated}/{len(ids)} tickets")
    return updated


def cmd_repair(category_label: str, ticket_id: int | None, with_images: bool) -> None:
    from models import Ticket
    db.init_db()
    if ticket_id is not None:
        ids = [ticket_id]
    else:
        with db.connect() as session:
            ids = [
                t.id for t in session.query(Ticket).all()
                if t.correct_index is None or _looks_like_dropped_short_answer(t.answers)
            ]
    if not ids:
        print("no tickets need a short-answer repair")
        return
    print(f"repairing {len(ids)} tickets")
    refresh_tickets(ids, category_label, with_images)


def main() -> None:
    parser = argparse.ArgumentParser(description="teoria.on.ge ticket scraper")
    parser.add_argument("command",
                        choices=["fetch", "parse", "scrape", "inspect", "check",
                                 "answers", "repair"])
    parser.add_argument("key_file", nargs="?", help="CSV file for the `answers` command")
    parser.add_argument("--category", type=int, default=2, help="2 = B, B1 (default)")
    parser.add_argument("--label", default="B", help="category label stored in the DB")
    parser.add_argument("--pages", type=int, default=47)
    parser.add_argument("--page", type=int, default=1, help="page for `inspect`")
    parser.add_argument("--ticket", type=int, help="ticket id for `repair`")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    parser.add_argument("--force", action="store_true", help="re-download cached pages")
    parser.add_argument("--no-images", action="store_true")
    args = parser.parse_args()

    if args.command in ("fetch", "scrape"):
        print(f"fetching {args.pages} pages of category {args.category}")
        fetch_pages(args.category, args.pages, args.delay, args.force)
    if args.command in ("parse", "scrape"):
        parse_cache(args.category, args.label, not args.no_images)
    if args.command == "inspect":
        cmd_inspect(args.category, args.page)
    if args.command == "check":
        cmd_check()
    if args.command == "answers":
        if not args.key_file:
            sys.exit("usage: python scraper.py answers key.csv")
        cmd_answers(args.key_file)
    if args.command == "repair":
        cmd_repair(args.label, args.ticket, not args.no_images)


if __name__ == "__main__":
    main()
