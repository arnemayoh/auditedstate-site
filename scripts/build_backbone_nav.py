#!/usr/bin/env python3
"""Insert Backbone Narrative navigation into one essay page.

Reads scripts/data/backbone-sequence.json. Rewrites only the essay passed
with --essay. A second run replaces the marked blocks instead of adding
another copy.

    python3 scripts/build_backbone_nav.py --essay 1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__file__).resolve().parent / "data" / "backbone-sequence.json"
ESSAYS = ROOT / "research" / "publications" / "essays"
SITE = "https://auditedstate.app"

TOP_START = "<!-- BACKBONE-NAV:START -->"
TOP_END = "<!-- BACKBONE-NAV:END -->"
END_START = "<!-- BACKBONE-ENDNAV:START -->"
END_END = "<!-- BACKBONE-ENDNAV:END -->"

TOP_RE = re.compile(
    rf"[ \t]*{re.escape(TOP_START)}.*?{re.escape(TOP_END)}\n?",
    re.S,
)
END_RE = re.compile(
    rf"[ \t]*{re.escape(END_START)}.*?{re.escape(END_END)}\n?",
    re.S,
)
BOX_RE = re.compile(r"[ \t]*<div class=\"sequence-box\">.*?</div>\n?", re.S)
WAYPOINTS_RE = re.compile(
    r"[ \t]*<(?:div|nav|section|aside) class=\"waypoints\">.*?</(?:div|nav|section|aside)>\n?",
    re.S,
)
MAIN_RE = re.compile(r"<main\b[^>]*>", re.I)
SUBTITLE_RE = re.compile(r"<p class=\"subtitle\">.*?</p>\n?", re.S)
LEAD_RE = re.compile(r"<p class=\"lead\">.*?</p>\n?", re.S)
H1_RE = re.compile(r"<h1\b[^>]*>.*?</h1>\n?", re.S)


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def load_sequence() -> list[dict]:
    entries = json.loads(DATA.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or len(entries) != 11:
        fail(f"{DATA} must list exactly 11 essays")
    numbers = [item["number"] for item in entries]
    if numbers != list(range(1, 12)):
        fail(f"{DATA} numbers must be 1 through 11 in order")
    for item in entries:
        for key in ("number", "title", "filename"):
            if key not in item:
                fail(f"{DATA} entry is missing {key}")
    return entries


def origin_for(text: str) -> str:
    if f"{SITE}/research/publications/essays/" in text:
        return SITE
    return ""


def href(origin: str, path: str) -> str:
    return f"{origin}{path}"


def indent_block(block: str, indent: str) -> str:
    lines = block.strip("\n").split("\n")
    return "\n".join((indent + line) if line else "" for line in lines) + "\n"


def render_top(entries: list[dict], index: int, origin: str) -> str:
    total = len(entries)
    current = entries[index]
    crumbs = [
        ("Research", "/research/"),
        ("Publications", "/research/publications/"),
        ("Essays", "/research/publications/essays/"),
    ]
    crumb_html = " › ".join(
        f'<a href="{escape(href(origin, path), quote=True)}">{escape(label)}</a>'
        for label, path in crumbs
    )
    crumb_html += " › Backbone Narrative"
    if index < total - 1:
        nxt = entries[index + 1]
        nxt_href = href(origin, f"/research/publications/essays/{nxt['filename']}")
        position = (
            f"Essay {current['number']} of {total} · Next: "
            f'<a href="{escape(nxt_href, quote=True)}">{escape(nxt["title"])}</a> →'
        )
    else:
        position = f"Essay {current['number']} of {total}"

    items: list[str] = []
    for item in entries:
        label = f"{item['number']}. {escape(item['title'])}"
        if item["number"] == current["number"]:
            items.append(f'      <li aria-current="page">{label}</li>')
        else:
            item_href = href(origin, f"/research/publications/essays/{item['filename']}")
            items.append(
                f'      <li><a href="{escape(item_href, quote=True)}">{label}</a></li>'
            )
    listing = "\n".join(items)
    return f"""{TOP_START}
<nav class="backbone-nav" aria-label="Backbone Narrative">
  <p class="backbone-breadcrumb">{crumb_html}</p>
  <p class="backbone-position">{position}</p>
  <details>
    <summary>All {total} essays</summary>
    <ol>
{listing}
    </ol>
  </details>
</nav>
{TOP_END}
"""


def render_end(entries: list[dict], index: int, origin: str) -> str:
    parts: list[str] = []
    if index > 0:
        prev = entries[index - 1]
        prev_href = href(origin, f"/research/publications/essays/{prev['filename']}")
        parts.append(f'<a href="{escape(prev_href, quote=True)}">← Prev</a>')
    if index < len(entries) - 1:
        nxt = entries[index + 1]
        nxt_href = href(origin, f"/research/publications/essays/{nxt['filename']}")
        parts.append(f'<a href="{escape(nxt_href, quote=True)}">Next →</a>')
    line = " · ".join(parts)
    return f"""{END_START}
<p class="backbone-pager">{line}</p>
{END_END}
"""


def content_indent(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("  <"):
            return "  "
    return ""


def place_top(text: str, block: str, indent: str) -> str:
    indented = indent_block(block, indent)
    if TOP_RE.search(text):
        return TOP_RE.sub(lambda _match: indented, text, count=1)
    if BOX_RE.search(text):
        return BOX_RE.sub(lambda _match: indented, text, count=1)
    if WAYPOINTS_RE.search(text):
        return WAYPOINTS_RE.sub(lambda _match: indented, text, count=1)
    main = MAIN_RE.search(text)
    if main is None:
        fail("no <main> element")
    intro = None
    for pattern in (SUBTITLE_RE, LEAD_RE, H1_RE):
        intro = pattern.search(text, main.end())
        if intro is not None:
            break
    if intro is None:
        fail("no subtitle, lead, or h1 to insert after")
    at = intro.end()
    spacer = "" if text[at : at + 1] == "\n" else "\n"
    return text[:at] + spacer + "\n" + indented + text[at:]


def place_end(text: str, block: str, indent: str) -> str:
    indented = indent_block(block, indent)
    if END_RE.search(text):
        return END_RE.sub(lambda _match: indented, text, count=1)
    close = text.rfind("</main>")
    if close < 0:
        fail("no </main> element")
    return text[:close] + indented + "\n" + text[close:]


def apply_essay(entries: list[dict], number: int) -> Path:
    match = next((item for item in entries if item["number"] == number), None)
    if match is None:
        fail(f"essay {number} is not in {DATA}")
    path = ESSAYS / match["filename"]
    if not path.is_file():
        fail(f"missing essay file {path}")
    original = path.read_text(encoding="utf-8")
    origin = origin_for(original)
    index = number - 1
    indent = content_indent(original)
    updated = place_top(original, render_top(entries, index, origin), indent)
    updated = place_end(updated, render_end(entries, index, origin), indent)
    if updated != original:
        path.write_text(updated, encoding="utf-8")
    print(path.relative_to(ROOT))
    return path


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--essay",
        type=int,
        required=True,
        help="Backbone essay number to update (1–11). One file only.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.essay <= 11:
        fail("--essay must be from 1 to 11")
    apply_essay(load_sequence(), args.essay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
