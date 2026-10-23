"""
Mention monitor: a read-only bot that tracks where Café Athena and its
competitors are talked about on public blogs, listicles, review roundups and
Reddit threads, and how they are described.

What it does
  - Reads a list of public URLs and RSS feeds from sources.json.
  - Checks robots.txt for each site and skips pages it disallows.
  - Waits between requests (default 5 seconds) and identifies itself.
  - Counts mentions of the target and each competitor, saves a short snippet,
    and labels how the target is framed on price (good value, pricey, ...).
  - Writes mentions.csv and a summary you can chart weekly.

What it never does
  - Post, comment, review, vote or log in anywhere.
  - Store usernames or personal data (snippets are trimmed to the sentence
    that names a restaurant).
  - Bypass robots.txt, paywalls or rate limits.

Usage:
    python mention_monitor.py sources.json --out mentions.csv
    python mention_monitor.py --demo        # runs on built-in sample pages, no network

sources.json example:
    {"target": ["Café Athena", "Cafe Athena"],
     "competitors": {"Spitz": ["Spitz"], "Mr. Shawarma": ["Mr. Shawarma"]},
     "urls": ["https://bestin-sandiego.com/best-mediterranean-restaurants-in-san-diego/"],
     "feeds": ["https://www.reddit.com/r/sandiego/search.rss?q=greek+pacific+beach&restrict_sr=1"]}
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
from compliance import mentions, value_perception  # noqa: E402

USER_AGENT = "AISO-Compass-MentionMonitor/1.0 (read-only research bot)"
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def allowed(url: str) -> bool:
    host = urlparse(url)
    base = f"{host.scheme}://{host.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser(base + "/robots.txt")
        try:
            rp.read()
            _robots[base] = rp
        except OSError:
            _robots[base] = None
    rp = _robots[base]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def get(url: str) -> str | None:
    if not allowed(url):
        print(f"  skipped (robots.txt): {url}")
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", errors="ignore")
    except OSError as e:
        print(f"  failed: {url} ({e})")
        return None


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    return re.sub(r"\s+", " ", unescape(re.sub(r"(?s)<[^>]+>", " ", html))).strip()


def feed_links(xml_text: str) -> list[str]:
    """Item links from RSS or Atom (Reddit search feeds are Atom)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    links = [e.text for e in root.iter("link") if e.text]
    links += [e.get("href") for e in root.iter("{http://www.w3.org/2005/Atom}link") if e.get("href")]
    return list(dict.fromkeys(l for l in links if l.startswith("http")))


def snippet(text: str, aliases: list[str], width: int = 240) -> str:
    for s in re.split(r"(?<!Mr\.)(?<!Dr\.)(?<!St\.)(?<=[.!?])\s+", text):
        if mentions(s, aliases):
            return s[:width]
    return ""


def scan_page(url: str, text: str, target: list[str], competitors: dict[str, list[str]]) -> list[dict]:
    rows, now = [], datetime.now(timezone.utc).isoformat(timespec="seconds")
    entities = {"__target__": target, **competitors}
    for name, aliases in entities.items():
        hits = sum(len(re.findall(re.escape(a.lower()), text.lower())) for a in aliases)
        if hits:
            rows.append({
                "checked_at": now, "url": url, "site": urlparse(url).netloc,
                "entity": "TARGET" if name == "__target__" else name, "mentions": hits,
                "framing": value_perception(text, aliases) if name == "__target__" else "",
                "snippet": snippet(text, aliases),
            })
    if not any(r["entity"] == "TARGET" for r in rows) and rows:
        rows.append({"checked_at": now, "url": url, "site": urlparse(url).netloc, "entity": "TARGET",
                     "mentions": 0, "framing": "not_mentioned", "snippet": ""})
    return rows


def run(cfg: dict, delay: float, pages: dict[str, str] | None = None) -> list[dict]:
    target, comps = cfg["target"], cfg.get("competitors", {})
    urls = list(cfg.get("urls", []))
    for f in cfg.get("feeds", []):
        xml_text = pages.get(f) if pages else get(f)
        if xml_text:
            urls += feed_links(xml_text)[:25]
        if not pages:
            time.sleep(delay)
    out = []
    for u in dict.fromkeys(urls):
        html = pages.get(u) if pages else get(u)
        if html:
            out += scan_page(u, html_to_text(html), target, comps)
        if not pages:
            time.sleep(delay)
    return out


def summarize(rows: list[dict]) -> dict:
    pages = {r["url"] for r in rows}
    with_target = {r["url"] for r in rows if r["entity"] == "TARGET" and r["mentions"] > 0}
    by = {}
    for r in rows:
        if r["mentions"]:
            by[r["entity"]] = by.get(r["entity"], 0) + 1
    return {"pages_with_any_restaurant": len(pages), "pages_naming_target": len(with_target),
            "target_page_share": round(len(with_target) / len(pages), 2) if pages else 0,
            "pages_per_entity": dict(sorted(by.items(), key=lambda kv: -kv[1]))}


DEMO = {
    "target": ["Café Athena", "Cafe Athena"],
    "competitors": {"Spitz": ["Spitz"], "Mr. Shawarma": ["Mr. Shawarma"]},
    "urls": ["https://example.com/best-pb", "https://example.com/happy-hour"],
}
DEMO_PAGES = {
    "https://example.com/best-pb": "<h1>Best Mediterranean in PB</h1><p>Spitz is a favorite. Mr. Shawarma is open late.</p>",
    "https://example.com/happy-hour": "<p>Cafe Athena has a great happy hour deal from 3 to 6. Spitz is fast.</p>",
}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="?", help="sources.json")
    ap.add_argument("--out", default="mentions.csv")
    ap.add_argument("--delay", type=float, default=5.0, help="seconds between requests (min 2)")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    if a.demo:
        rows = run(DEMO, 0, DEMO_PAGES)
    elif a.sources:
        rows = run(json.loads(Path(a.sources).read_text(encoding="utf-8")), max(2.0, a.delay))
    else:
        sys.exit("Pass sources.json or --demo.")
    if rows:
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    print(json.dumps(summarize(rows), indent=2))
    print(f"Saved {len(rows)} rows to {a.out}")
