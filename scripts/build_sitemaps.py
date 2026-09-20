#!/usr/bin/env python3
"""Generate sitemap.xml and child sitemaps with substantive <lastmod> dates.

Run from anywhere; paths are resolved relative to the repo root (parent of
scripts/). Exits non-zero and writes nothing if self-checks fail. After a
successful write, child sitemaps that are no longer in CHILD_ORDER are removed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://auditedstate.app"
NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

CHILD_ORDER = [
    "sitemap-core.xml",
    "sitemap-concepts.xml",
    "sitemap-architecture.xml",
    "sitemap-philosophy.xml",
    "sitemap-essays.xml",
    "sitemap-research.xml",
    "sitemap-about.xml",
    "sitemap-structural-alignment.xml",
]

# Longest prefix first. Root-level pages, labs/, and concept-map.html → core.
PREFIX_TO_CHILD = [
    ("research/publications/essays/", "sitemap-essays.xml"),
    ("research/", "sitemap-research.xml"),
    ("about/", "sitemap-about.xml"),
    ("structural-alignment/", "sitemap-structural-alignment.xml"),
    ("concepts/", "sitemap-concepts.xml"),
    ("architecture/", "sitemap-architecture.xml"),
    ("philosophy/", "sitemap-philosophy.xml"),
    ("labs/", "sitemap-core.xml"),
]

SPOT_CHECK = [
    "index.html",
    "about/charter.html",
    "concepts/index.html",
    "labs/index.html",
    "concepts/promotion-gate.html",
    "philosophy/hand-on-the-rudder.html",
    "structural-alignment/eu-ai-act-structural-alignment.html",
    "research/entry/index.html",
    "research/citation-index.html",
]

LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.I)
META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
ATTR_RE = re.compile(r"""\b([a-zA-Z_:][\w:.-]*)\s*=\s*(['"])(.*?)\2""", re.S)


def attr(tag: str, name: str) -> str | None:
    name = name.lower()
    for key, _quote, value in ATTR_RE.findall(tag):
        if key.lower() == name:
            return value
    return None


def read_commented_lines(path: Path) -> list[str]:
    items: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            items.append(line)
    return items


def load_sweep_hashes() -> set[str]:
    hashes: set[str] = set()
    for line in (ROOT / "audit/sweep-commits.txt").read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        hashes.add(stripped.split()[0].lower())
    return hashes


def url_for(path: str) -> str:
    if path == "index.html":
        return f"{SITE}/"
    if path.endswith("/index.html"):
        return f"{SITE}/{path[: -len('index.html')]}"
    return f"{SITE}/{path}"


def path_for_url(loc: str) -> str | None:
    if not loc.startswith(SITE):
        return None
    rest = loc[len(SITE) :]
    if rest in ("", "/"):
        return "index.html"
    if rest.endswith("/"):
        return rest.strip("/") + "/index.html"
    return rest.lstrip("/")


def child_for(path: str) -> str | None:
    if path == "concept-map.html" or "/" not in path:
        return "sitemap-core.xml"
    for prefix, child in PREFIX_TO_CHILD:
        if path.startswith(prefix):
            return child
    return None


def parse_html_head(path: Path) -> tuple[str | None, str | None, bool]:
    text = path.read_text(encoding="utf-8", errors="replace")
    canonical = None
    for tag in LINK_TAG_RE.findall(text):
        rel = attr(tag, "rel")
        if rel and rel.lower().strip() == "canonical":
            canonical = attr(tag, "href")
            break
    robots = None
    refresh = False
    for tag in META_TAG_RE.findall(text):
        http_equiv = attr(tag, "http-equiv")
        if http_equiv and http_equiv.lower() == "refresh":
            refresh = True
        name = attr(tag, "name")
        if name and name.lower() == "robots":
            robots = attr(tag, "content") or ""
    return canonical, robots, refresh


def is_stub(canonical: str | None, refresh: bool, emitted_url: str) -> str | None:
    if refresh:
        return "stub (meta-refresh)"
    if canonical is not None and canonical != emitted_url:
        return f"stub (canonical points at {canonical})"
    return None


def has_noindex(robots: str | None) -> bool:
    if not robots:
        return False
    return re.search(r"\bnoindex\b", robots, re.I) is not None


def git_output(args: list[str]) -> str:
    result = subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout


def _is_skipped(commit: dict) -> bool:
    return bool(commit.get("sweep") or commit.get("exact_rename"))


def file_history(relpath: str, sweep: set[str]) -> list[dict]:
    """Walk git log --follow -M --name-status for one file.

    A commit is skipped for dating if it is a listed sweep, its subject
    starts with [sweep], or git records this file as an exact rename (R100).
    A rename with any content change (R099 and below) still counts.
    """
    out = git_output(
        [
            "git",
            "-c",
            "core.quotepath=false",
            "log",
            "--follow",
            "-M",
            "--name-status",
            "--format=%H%x00%aI%x00%s",
            "--",
            relpath,
        ]
    )
    commits: list[dict] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            commits.append(current)
            current = None

    for line in out.splitlines():
        if "\0" in line:
            parts = line.split("\0")
            if len(parts) == 3 and re.fullmatch(r"[0-9a-f]{40}", parts[0]):
                flush()
                commit_hash, author_date, subject = parts
                current = {
                    "hash": commit_hash,
                    "date": author_date,
                    "subject": subject,
                    "sweep": commit_hash.lower() in sweep or subject.startswith("[sweep]"),
                    "rename_from": None,
                    "exact_rename": False,
                    "status": None,
                }
                continue
        if current is None or not line.strip():
            continue
        bits = line.split("\t")
        status = bits[0]
        current["status"] = status
        match = re.fullmatch(r"R(\d{3})", status)
        if match and len(bits) >= 3:
            current["rename_from"] = bits[1]
            current["exact_rename"] = match.group(1) == "100"
    flush()
    return commits


def lastmod_from_history(commits: list[dict]) -> str | None:
    if not commits:
        return None
    chosen = next((c for c in commits if not _is_skipped(c)), None)
    if chosen is None:
        chosen = commits[-1]
    return to_utc_date(chosen["date"])


def to_utc_date(iso: str) -> str:
    text = iso.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date().isoformat()


def xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def render_urlset(entries: list[dict]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<urlset xmlns="{NS}">',
        "",
    ]
    for entry in entries:
        lines.append("  <url>")
        lines.append(f"    <loc>{xml_escape(entry['loc'])}</loc>")
        if entry.get("lastmod"):
            lines.append(f"    <lastmod>{xml_escape(entry['lastmod'])}</lastmod>")
        lines.append("  </url>")
    lines.append("")
    lines.append("</urlset>")
    lines.append("")
    return "\n".join(lines)


def render_index(children: list[dict]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<sitemapindex xmlns="{NS}">',
        "",
    ]
    for child in children:
        lines.append("  <sitemap>")
        lines.append(f"    <loc>{xml_escape(child['loc'])}</loc>")
        if child.get("lastmod"):
            lines.append(f"    <lastmod>{xml_escape(child['lastmod'])}</lastmod>")
        lines.append("  </sitemap>")
    lines.append("")
    lines.append("</sitemapindex>")
    lines.append("")
    return "\n".join(lines)


def parse_xml(text: str) -> ET.Element:
    return ET.fromstring(text)


def iter_html_files() -> list[str]:
    paths: list[str] = []
    for path in ROOT.rglob("*.html"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(".git/"):
            continue
        paths.append(rel)
    return sorted(paths)


def fail(messages: list[str]) -> None:
    print("Self-check failed; writing nothing.", file=sys.stderr)
    for message in messages:
        print(f"  - {message}", file=sys.stderr)
    sys.exit(1)


def self_check(generated: dict[str, str], emitted: list[dict]) -> list[str]:
    errors: list[str] = []
    index_name = "sitemap.xml"
    if index_name not in generated:
        errors.append("index sitemap.xml was not generated")
        return errors

    try:
        index_root = parse_xml(generated[index_name])
    except ET.ParseError as exc:
        errors.append(f"sitemap.xml is not well-formed: {exc}")
        return errors

    index_ns = index_root.tag.split("}")[0][1:] if index_root.tag.startswith("{") else ""
    if index_ns != NS:
        errors.append(f"sitemap.xml namespace is {index_ns!r}, expected {NS!r}")
    if not index_root.tag.endswith("sitemapindex"):
        errors.append(f"sitemap.xml root is {index_root.tag}")

    listed: list[str] = []
    for sitemap in index_root:
        tag = sitemap.tag.split("}")[-1]
        if tag != "sitemap":
            continue
        loc = None
        for child in sitemap:
            if child.tag.split("}")[-1] == "loc" and child.text:
                loc = child.text.strip()
        if not loc:
            errors.append("index sitemap entry missing <loc>")
            continue
        name = loc.rsplit("/", 1)[-1]
        listed.append(name)

    if listed != CHILD_ORDER:
        errors.append(
            f"index children {listed} do not match required order {CHILD_ORDER}"
        )

    for name, text in generated.items():
        try:
            root = parse_xml(text)
        except ET.ParseError as exc:
            errors.append(f"{name} is not well-formed: {exc}")
            continue
        ns = root.tag.split("}")[0][1:] if root.tag.startswith("{") else ""
        if ns != NS:
            errors.append(f"{name} namespace is {ns!r}, expected {NS!r}")

    locs = [entry["loc"] for entry in emitted]
    if len(locs) != len(set(locs)):
        seen: set[str] = set()
        dupes = []
        for loc in locs:
            if loc in seen:
                dupes.append(loc)
            seen.add(loc)
        errors.append(f"duplicate <loc> values: {dupes}")

    for entry in emitted:
        loc = entry["loc"]
        rel = path_for_url(loc)
        if rel is None:
            errors.append(f"{loc} is not under {SITE}")
            continue
        path = ROOT / rel
        if not path.is_file():
            errors.append(f"{loc} maps to missing file {rel}")
            continue
        canonical, _robots, _refresh = parse_html_head(path)
        if canonical != loc:
            errors.append(f"{loc} canonical is {canonical!r}")

    generated_children = sorted(name for name in generated if name != index_name)
    if generated_children != sorted(CHILD_ORDER):
        errors.append(
            f"generated children {generated_children} != {sorted(CHILD_ORDER)}"
        )
    if set(listed) != set(generated_children):
        errors.append("index children and generated children differ")

    return errors


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate sitemap.xml and child sitemaps with substantive lastmod dates."
        )
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print rename report and lastmod spot-check chains.",
    )
    return parser.parse_args(argv)


def retire_stale_children() -> list[str]:
    """Remove sitemap-*.xml files that are no longer in CHILD_ORDER."""
    intended = set(CHILD_ORDER)
    removed: list[str] = []
    for path in sorted(ROOT.glob("sitemap-*.xml")):
        if path.name not in intended:
            path.unlink()
            removed.append(path.name)
    return removed


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sweep = load_sweep_hashes()
    baseline = read_commented_lines(ROOT / "scripts/sitemap-baseline.txt")
    approved = read_commented_lines(ROOT / "scripts/sitemap-approved.txt")
    exclude = set(read_commented_lines(ROOT / "scripts/sitemap-exclude.txt"))

    candidates: list[str] = []
    seen: set[str] = set()
    for path in baseline + approved:
        if path in exclude or path in seen:
            continue
        seen.add(path)
        candidates.append(path)

    skipped: list[tuple[str, str]] = []
    unmapped: list[str] = []
    emitted: list[dict] = []
    histories: dict[str, list[dict]] = {}
    renames: list[tuple[str, str, str]] = []

    for path in candidates:
        child = child_for(path)
        if child is None:
            unmapped.append(path)
            continue
        full = ROOT / path
        emitted_url = url_for(path)
        if not full.is_file():
            skipped.append((path, "does not exist"))
            continue
        canonical, robots, refresh = parse_html_head(full)
        stub_reason = is_stub(canonical, refresh, emitted_url)
        if stub_reason:
            skipped.append((path, stub_reason))
            continue
        if canonical is None:
            skipped.append((path, "no <link rel=canonical>"))
            continue
        if canonical != emitted_url:
            skipped.append((path, f"canonical is {canonical!r}, expected {emitted_url!r}"))
            continue
        if has_noindex(robots):
            skipped.append((path, f"robots noindex ({robots})"))
            continue

        history = file_history(path, sweep)
        histories[path] = history
        lastmod = lastmod_from_history(history)
        for commit in history:
            if commit.get("rename_from"):
                renames.append((path, commit["rename_from"], commit["hash"]))
        emitted.append(
            {
                "path": path,
                "loc": emitted_url,
                "lastmod": lastmod,
                "child": child,
            }
        )

    emitted.sort(key=lambda item: item["loc"])

    grouped: dict[str, list[dict]] = {name: [] for name in CHILD_ORDER}
    for entry in emitted:
        grouped[entry["child"]].append(entry)

    generated: dict[str, str] = {}
    index_children: list[dict] = []
    for name in CHILD_ORDER:
        generated[name] = render_urlset(grouped[name])
        lastmods = [e["lastmod"] for e in grouped[name] if e.get("lastmod")]
        index_children.append(
            {
                "loc": f"{SITE}/{name}",
                "lastmod": max(lastmods) if lastmods else None,
            }
        )
    generated["sitemap.xml"] = render_index(index_children)

    errors = self_check(generated, emitted)
    if errors:
        fail(errors)

    for name, text in generated.items():
        (ROOT / name).write_text(text, encoding="utf-8")
    stale = retire_stale_children()

    emitted_paths = {entry["path"] for entry in emitted}
    listed = set(baseline) | set(approved) | exclude
    neither: list[str] = []
    for path in iter_html_files():
        if path in listed:
            continue
        full = ROOT / path
        canonical, _robots, refresh = parse_html_head(full)
        if is_stub(canonical, refresh, url_for(path)):
            continue
        neither.append(path)

    print("== Per child sitemap ==")
    for name in CHILD_ORDER:
        rows = grouped[name]
        lastmods = [e["lastmod"] for e in rows if e.get("lastmod")]
        missing = sum(1 for e in rows if not e.get("lastmod"))
        earliest = min(lastmods) if lastmods else "—"
        latest = max(lastmods) if lastmods else "—"
        print(f"{name}\t{len(rows)}\t{earliest}\t{latest}\tno_lastmod={missing}")

    print()
    print(f"== Totals ==\nemitted={len(emitted)} baseline={len(baseline)} approved={len(approved)} exclude={len(exclude)}")
    delta = len(emitted) - (len(baseline) + len(approved))
    print(f"emitted - (baseline + approved) = {delta}")

    dates = [e["lastmod"] for e in emitted if e.get("lastmod")]
    distinct = sorted(set(dates))
    clusters: dict[str, int] = defaultdict(int)
    for date in dates:
        clusters[date] += 1
    if clusters:
        top_date, top_count = max(clusters.items(), key=lambda kv: (kv[1], kv[0]))
        pct = 100.0 * top_count / len(emitted)
        print()
        print("== Distribution ==")
        print(f"distinct_lastmod_dates={len(distinct)}")
        print(f"largest_cluster={top_date} count={top_count} pct={pct:.1f}")
        if pct > 50:
            print("WARNING: more than 50% of pages share one lastmod date.")

    print()
    print("== Skipped pages ==")
    if not skipped:
        print("(none)")
    else:
        for path, reason in skipped:
            print(f"{path}\t{reason}")

    print()
    print("== Unmapped ==")
    if not unmapped:
        print("(none)")
    else:
        for path in unmapped:
            print(path)

    print()
    print("== Real pages neither emitted nor excluded (author decision needed) ==")
    if not neither:
        print("(none)")
    else:
        for path in neither:
            print(path)

    if stale:
        print()
        print("== Removed stale child sitemaps ==")
        for name in stale:
            print(name)

    if not args.verbose:
        return 0

    print()
    print("== Rename report ==")
    if not renames:
        print("(none)")
    else:
        for path, old, commit in renames:
            status = ""
            for item in histories.get(path, []):
                if item["hash"] == commit:
                    status = item.get("status") or ""
                    break
            skip = " SKIP-R100" if status == "R100" else ""
            print(f"{path}\trenamed from {old}\tin {commit}\t{status}{skip}")

    print()
    print("== Spot-check chains ==")
    for path in SPOT_CHECK:
        print(f"--- {path} ---")
        history = histories.get(path)
        if history is None:
            history = file_history(path, sweep)
        if not history:
            print("  (no git history; lastmod omitted)")
            continue
        lastmod = lastmod_from_history(history)
        chosen = next((c for c in history if not _is_skipped(c)), history[-1])
        for commit in history:
            if commit["sweep"]:
                flag = "SKIP-SWEEP"
            elif commit.get("exact_rename"):
                flag = "SKIP-R100"
            else:
                flag = "consider"
            extra = ""
            if commit.get("rename_from"):
                extra = f"  {commit.get('status')} from {commit['rename_from']}"
            print(
                f"  {flag}  {commit['hash']}  {commit['date']}  {commit['subject']}{extra}"
            )
        print(f"  lastmod={lastmod}  from {chosen['hash']}")

    _ = emitted_paths
    return 0


if __name__ == "__main__":
    sys.exit(main())
