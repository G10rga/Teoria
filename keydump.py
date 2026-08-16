"""Dump everything about the answer key on page 1. Paste the output back.

    python keydump.py

Also writes keydump.txt (same content) and keyblock.html (one full ticket).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup, Tag

BASE = Path(__file__).resolve().parent
SRC = BASE / "html_cache" / "cat2_p01.html"
lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    lines.append(text)


def attrs_of(node: Tag) -> str:
    return json.dumps({k: (v if isinstance(v, str) else " ".join(v))[:60]
                       for k, v in node.attrs.items()}, ensure_ascii=False)[:400]


def main() -> None:
    if not SRC.exists():
        say(f"missing {SRC} - run `python scraper.py fetch` first")
        return
    soup = BeautifulSoup(SRC.read_text(encoding="utf-8"), "lxml")

    # 1. the key attribute itself -------------------------------------------
    nodes = soup.select("[data-is-correct-list]")
    say(f"== [data-is-correct-list] nodes: {len(nodes)} ==")
    values = [n.get("data-is-correct-list", "") for n in nodes]
    say(f"raw values (all): {json.dumps(values, ensure_ascii=False)[:700]}")
    say(f"value lengths: {[len(v) for v in values]}")
    for i, node in enumerate(nodes[:2]):
        say(f"-- node {i}: <{node.name}> class={' '.join(node.get('class') or [])}")
        say(f"   attrs: {attrs_of(node)}")
        say(f"   .t-answer inside: {len(node.select('.t-answer'))}")

    # 2. every data-* attribute used anywhere -------------------------------
    census: dict[str, int] = {}
    samples: dict[str, str] = {}
    for node in soup.find_all(True):
        for key, val in node.attrs.items():
            if not key.startswith("data-"):
                continue
            census[key] = census.get(key, 0) + 1
            if key not in samples:
                text = val if isinstance(val, str) else " ".join(val)
                samples[key] = f"<{node.name}> = {text[:70]!r}"
    say("\n== all data-* attributes ==")
    for key, count in sorted(census.items(), key=lambda kv: -kv[1])[:25]:
        say(f"  {key} x{count}  first: {samples[key]}")

    # 3. one answer group, verbatim -----------------------------------------
    answers = soup.select(".t-answer")
    say(f"\n== .t-answer nodes: {len(answers)} ==")
    for node in answers[:4]:
        say(f"  attrs: {attrs_of(node)}")
        inner = node.select_one(".t-answer-inner")
        if inner:
            say(f"    inner attrs: {attrs_of(inner)}")
    if answers:
        say("\n-- first .t-answer verbatim --")
        say(str(answers[0])[:900])

    # 4. scripts that might carry the key ------------------------------------
    say("\n== scripts mentioning correct/answer/ticket ==")
    hits = 0
    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        if re.search(r"correct|isCorrect|answer|t-a-", text, re.I):
            hits += 1
            say(f"-- script ({len(text)} chars) src={script.get('src')}")
            say("   " + re.sub(r"\s+", " ", text)[:600])
            if hits >= 4:
                break
    if not hits:
        say("  none")
    say("\n== external script srcs ==")
    for script in soup.find_all("script", src=True)[:15]:
        say(f"  {script['src']}")

    # 5. anything that looks like a marked-correct class ---------------------
    say("\n== classes containing correct/right/true/success/green ==")
    found: dict[str, int] = {}
    for node in soup.find_all(class_=True):
        for name in node.get("class") or []:
            if re.search(r"correct|right|true|success|green|check", name, re.I):
                found[name] = found.get(name, 0) + 1
    say("  " + (", ".join(f"{k}({v})" for k, v in sorted(found.items())) or "none"))

    # 6. save one whole ticket block ----------------------------------------
    if nodes:
        block = nodes[0]
        for _ in range(6):
            parent = block.parent
            if not isinstance(parent, Tag) or parent.name in ("body", "html"):
                break
            if len(parent.select("[data-is-correct-list]")) > 1:
                break
            block = parent
        (BASE / "keyblock.html").write_text(str(block), encoding="utf-8")
        say(f"\nwrote keyblock.html ({len(str(block)):,} chars)")

    (BASE / "keydump.txt").write_text("\n".join(lines), encoding="utf-8")
    say("wrote keydump.txt")


if __name__ == "__main__":
    main()
