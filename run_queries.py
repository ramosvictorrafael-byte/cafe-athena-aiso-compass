"""
Run a query bank through an AI model and save every raw answer, so the
weekly GitHub Action can track Café Athena's AI visibility over time.

Usage:
    ANTHROPIC_API_KEY=... python run_queries.py data/query_bank_90.json --out reports/run.json
    python run_queries.py data/query_bank_90.json --dry-run    # no key needed, checks the bank

Environment:
    ANTHROPIC_API_KEY   required for a real run (set as a GitHub Actions secret)
    AISO_MODEL          model ID to use (default below); keep it fixed between
                        runs so results stay comparable

The model answers from its own knowledge (API mode). That measures model
knowledge, not live Google, ChatGPT Search or Gemini Search rankings.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from compliance import (TARGET_ALIASES, mentions, explicit_rank,  # noqa: E402
                        value_perception, mode_label)

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
PROMPT = ("You are a helpful local dining assistant. A person in San Diego asks: \"{q}\". "
          "Recommend specific restaurants by name as a numbered list, best first, "
          "with one line on why for each.")


def ask(q: str, model: str, key: str) -> str:
    body = json.dumps({"model": model, "max_tokens": 600,
                       "messages": [{"role": "user", "content": PROMPT.format(q=q)}]}).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bank")
    ap.add_argument("--out", default="reports/run.json")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between API calls")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    bank = json.loads(Path(a.bank).read_text(encoding="utf-8"))
    print(f"{len(bank)} queries loaded from {a.bank}")
    if a.dry_run:
        return
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY not set; skipping the AI run.")
        return
    model = os.environ.get("AISO_MODEL", DEFAULT_MODEL)
    run_id = datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M")
    rows = []
    for q in bank:
        try:
            answer = ask(q["query_text"], model, key)
        except OSError as e:
            answer = ""
            print(f"  {q['query_id']}: failed ({e})")
        rows.append({
            "run_id": run_id, "query_id": q["query_id"], "query_text": q["query_text"],
            "group": q.get("group") or ",".join(q.get("intents", [])),
            "target": "Café Athena", "model_id": model, "mode": "api",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "raw_answer": answer,
            "mentioned": mentions(answer, TARGET_ALIASES),
            "rank": explicit_rank(answer, TARGET_ALIASES),
            "framing": value_perception(answer, TARGET_ALIASES),
        })
        time.sleep(a.delay)

    n = len(rows)
    organic = [r for r in rows if "athena" not in r["query_text"].lower()]
    summary = {
        "run_id": run_id, "model_id": model, "label": mode_label("api"),
        "queries": n,
        "mention_rate": round(sum(r["mentioned"] for r in rows) / n, 4),
        "organic_mention_rate": round(sum(r["mentioned"] for r in organic) / len(organic), 4) if organic else None,
        "top3_rate": round(sum(1 for r in rows if r["rank"] and r["rank"] <= 3) / n, 4),
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "observations": rows}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
