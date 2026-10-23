# Café Athena AI Visibility (AISO Compass)

NYU SPS x Pulse Foundry hackathon, October 23, 2026. Target restaurant: Café Athena, 1846 Garnet Ave, Pacific Beach, San Diego.

This repo answers the brief's loop: Discovery, Analysis, Action, Measurement. GitHub Actions runs the tests on every push and runs the visibility checks every week.

## What runs automatically

| Workflow | When | What it does |
| --- | --- | --- |
| Tests (`ci.yml`) | Every push and pull request | Runs 17 compliance tests, checks both query banks, runs the monitor demo |
| Weekly AI visibility check (`weekly-visibility.yml`) | Mondays, or on demand from the Actions tab | Scores cafeathena.com for AI-readable keywords, scans public pages and Reddit feeds for mentions, runs the 90 queries through the Claude API, and commits the results to `reports/<date>/` |

## Files

| File | Purpose |
| --- | --- |
| `compliance.py` | Stage gates, KPIs, share of voice, value for money lens, FAQ and schema builders, approval-gated export |
| `run_queries.py` | Runs a query bank through the model and saves every raw answer |
| `keyword_coverage.py` | ATS-style check: are the phrases customers ask AI written as plain text on the site? |
| `mention_monitor.py` | Read-only bot: who is named on listicles, blogs and Reddit, and how Café Athena is framed on price |
| `data/query_bank_cafe_athena.json` | 24-query core bank for the demo |
| `data/query_bank_90.json` | 90-query bank for weekly tracking |
| `data/sources.json` | Pages and feeds the monitor reads |
| `test_compliance.py` | Tests |

## Setup (5 minutes)

1. Upload these files to a new GitHub repository (keep the `.github` folder).
2. To enable the AI run: Settings, Secrets and variables, Actions, New repository secret named `ANTHROPIC_API_KEY`.
3. Optional: add a repository variable `AISO_MODEL` to pin the model. Keep it fixed so weekly runs stay comparable.
4. Actions tab, "Weekly AI visibility check", Run workflow, to get the first report now.

## Run locally

```
pip install pytest
python -m pytest -q
python keyword_coverage.py https://cafeathena.com/
python mention_monitor.py data/sources.json --out mentions.csv
ANTHROPIC_API_KEY=... python run_queries.py data/query_bank_cafe_athena.json --out run.json
```

## Ground rules

- API mode measures model knowledge, not live Google, ChatGPT Search or Gemini Search rankings. Synthetic runs are always labeled.
- Comparable runs need the same query bank, target, model ID and mode. Even then, a change does not prove a content edit caused it.
- Content uses only verified facts with a source. No invented prices, hours, capacity or amenities.
- Export needs owner approval and goes to a local folder; nothing is published to the restaurant's site automatically.
- The monitor reads public pages only, follows robots.txt, waits between requests, and never posts, reviews or logs in. No fake reviews or undisclosed promotion.
