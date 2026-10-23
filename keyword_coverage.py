"""
Keyword coverage checker for Café Athena: works like a resume ATS scan.

An ATS checks whether a resume contains the phrases a job post asks for.
This checks whether a web page contains, as plain readable text, the phrases
customers use when they ask AI where to eat. Text inside images or PDFs does
not count, because AI tools mostly cannot read it.

Usage:
    python keyword_coverage.py https://cafeathena.com/
    python keyword_coverage.py saved_page.html
    python keyword_coverage.py page.html --json report.json

Standard library only. Respects robots.txt and sends one request.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

USER_AGENT = "AISO-Compass-KeywordCheck/1.0 (hackathon research; contact: team)"

# Phrase groups, weighted by how often the matching intent appears in the
# 90-query bank. Each phrase lists acceptable variants (any one counts); a variant starting
# with "re:" is a regular expression.
PHRASES: dict[str, dict] = {
    "cuisine_location": {"weight": 3, "phrases": {
        "Greek restaurant in Pacific Beach": ["greek restaurant in pacific beach", "pacific beach greek restaurant"],
        "Mediterranean": ["mediterranean"],
        "Pacific Beach": ["pacific beach"],
        "San Diego": ["san diego"],
        "Garnet Ave": ["garnet ave", "garnet avenue"],
        "gyro": ["gyro"], "souvlaki": ["souvlaki"], "moussaka": ["moussaka"],
    }},
    "value": {"weight": 3, "phrases": {
        "price shown as text": [r"re:\$\s?\d{1,3}"],
        "affordable or good value wording": ["affordable", "good value", "great value", "reasonably priced", "fair price"],
        "portion or deal wording": ["generous portions", "deal", "special"],
    }},
    "happy_hour": {"weight": 3, "phrases": {
        "happy hour": ["happy hour"],
        "3 to 6 PM": ["3 to 6", "3-6", "3 pm", "3:00"],
        "daily": ["daily", "every day"],
        "small plates or tapas": ["small plates", "tapas"],
    }},
    "dietary": {"weight": 2, "phrases": {
        "gluten-free": ["gluten-free", "gluten free"],
        "vegan": ["vegan"], "vegetarian": ["vegetarian"],
    }},
    "occasion_group": {"weight": 2, "phrases": {
        "private events": ["private event", "private party"],
        "rehearsal dinner": ["rehearsal dinner"],
        "catering": ["catering"], "group dining": ["group dining", "large group", "groups"],
    }},
    "date_night": {"weight": 1, "phrases": {
        "cocktails": ["cocktail"], "Greek mythology theme": ["mythology"], "date night": ["date night"],
    }},
    "local_trust": {"weight": 1, "phrases": {
        "since 1990": ["since 1990", "1990"], "locally owned or family": ["family", "locally owned", "local favorite"],
    }},
}


class _TextExtractor(HTMLParser):
    """Collects visible text and notes images, PDFs and JSON-LD."""

    def __init__(self):
        super().__init__()
        self.text, self.jsonld, self.images, self.pdfs = [], [], 0, 0
        self._skip = 0
        self._in_ld = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and a.get("type") == "application/ld+json":
            self._in_ld = True
        elif tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "img":
            self.images += 1
        elif tag == "a" and str(a.get("href", "")).lower().endswith(".pdf"):
            self.pdfs += 1

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self._in_ld = False
        elif tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in_ld:
            self.jsonld.append(data)
        elif not self._skip:
            self.text.append(data)


def fetch(source: str) -> str:
    if not source.startswith(("http://", "https://")):
        return Path(source).read_text(encoding="utf-8", errors="ignore")
    parts = urlparse(source)
    rp = urllib.robotparser.RobotFileParser(f"{parts.scheme}://{parts.netloc}/robots.txt")
    try:
        rp.read()
        if not rp.can_fetch(USER_AGENT, source):
            sys.exit(f"robots.txt disallows {source}; save the page by hand and pass the file instead.")
    except OSError:
        pass  # no robots.txt reachable: proceed with a single polite request
    req = urllib.request.Request(source, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="ignore")


def analyze(html: str) -> dict:
    p = _TextExtractor()
    p.feed(html)
    text = re.sub(r"\s+", " ", " ".join(p.text)).lower()
    types = []
    for blob in p.jsonld:
        try:
            data = json.loads(blob)
            for d in data if isinstance(data, list) else [data]:
                t = d.get("@type")
                types += t if isinstance(t, list) else [t]
        except (json.JSONDecodeError, AttributeError):
            types.append("invalid JSON-LD")

    groups, earned, possible = {}, 0, 0
    for g, spec in PHRASES.items():
        found, missing = [], []
        for label, variants in spec["phrases"].items():
            hit = any(re.search(v[3:], text) if v.startswith("re:") else v in text
                      for v in variants)
            (found if hit else missing).append(label)
        n = len(spec["phrases"])
        earned += spec["weight"] * len(found)
        possible += spec["weight"] * n
        groups[g] = {"found": found, "missing": missing, "coverage": round(len(found) / n, 2)}

    return {
        "score": round(100 * earned / possible),
        "groups": groups,
        "structured_data": types or ["none"],
        "has_restaurant_schema": any(t in ("Restaurant", "FoodEstablishment") for t in types),
        "has_faq_schema": "FAQPage" in types,
        "images": p.images,
        "pdf_links": p.pdfs,
        "word_count": len(text.split()),
    }


def print_report(r: dict) -> None:
    print(f"\nAI readability score: {r['score']} / 100   ({r['word_count']} words of readable text)")
    print(f"Structured data: {', '.join(map(str, r['structured_data']))}")
    print(f"Images: {r['images']}   PDF links: {r['pdf_links']}  (content inside these is not counted)\n")
    for g, d in r["groups"].items():
        print(f"{g:18} {int(d['coverage'] * 100):3}%   missing: {', '.join(d['missing']) or 'nothing'}")
    print("\nTop fixes:")
    if not r["has_restaurant_schema"]:
        print("- Add Restaurant JSON-LD (build_restaurant_jsonld in compliance.py).")
    if not r["has_faq_schema"]:
        print("- Add an FAQ page with FAQPage JSON-LD (draft_faq_for_gaps + build_faq_jsonld).")
    for g in sorted(r["groups"], key=lambda k: -PHRASES[k]["weight"]):
        if r["groups"][g]["missing"]:
            print(f"- Write {', '.join(r['groups'][g]['missing'])} into the page as plain text ({g}).")
            break


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="URL or saved HTML file")
    ap.add_argument("--json", help="also save the report as JSON")
    a = ap.parse_args()
    report = analyze(fetch(a.source))
    print_report(report)
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
