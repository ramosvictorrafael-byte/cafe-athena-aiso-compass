"""
AISO Compass compliance module.

Encodes the stage gates and definitions of done from the team hub so the app
can enforce them in code, not just in the pitch:

  Stage 1  Discovery    : a run = exactly 24 fixed queries, raw answers saved,
                          timestamp + model ID + mode recorded, mode labeled.
  Stage 2  Analysis     : mention rate, explicit top-3 rate, average explicit
                          rank, competitor counts, each tied to a query ID.
  Stage 3  Action       : every proposed claim cites a source; no invented
                          prices, hours, capacity or amenities; export only
                          after owner approval, and only to a local folder.
  Stage 4  Measurement  : two runs are comparable only with the same query
                          bank, target, model ID and mode; the causality
                          caveat is always shown.

Target restaurant (required by the brief): Café Athena, Pacific Beach, San Diego.
Team focus: Value for money (value_report) and Content Creator
(draft_faq_for_gaps, build_faq_jsonld, readiness_scorecard).

Drop this file next to app.py and import what you need. Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Fixed rules (change only if the team changes the plan)
# ---------------------------------------------------------------------------

QUERY_BANK_SIZE = 24
VALID_MODES = {"synthetic", "api"}

MODE_LABELS = {
    "synthetic": (
        "SYNTHETIC DATA: generated for demonstration. "
        "This is not measured AI visibility."
    ),
    "api": (
        "MODEL KNOWLEDGE (API mode): answers from the model's built-in knowledge. "
        "This is not live Google, ChatGPT Search or Gemini Search ranking."
    ),
}

CAUSALITY_CAVEAT = (
    "These runs are comparable (same query bank, target, model ID and mode), "
    "but a difference between them does not prove that a content change caused it."
)

# Claims that must never appear unless they come from a verified, sourced fact.
HIGH_RISK_FIELDS = {
    "priceRange", "openingHours", "openingHoursSpecification",
    "maximumAttendeeCapacity", "amenityFeature", "acceptsReservations",
    "menu", "hasMenu", "telephone", "address", "starRating", "aggregateRating",
}

JSONLD_REQUIRED = {"@context", "@type", "name"}

# ---------------------------------------------------------------------------
# Required target (from the official brief). Research must re-confirm each
# fact on cafeathena.com and replace the source URL with the exact page.
# ---------------------------------------------------------------------------

BRIEF_URL = "https://github.com/DJPulseFoundry/Pulse-Foundry-X-NYU-SPS-Hackathon"
TARGET_NAME = "Café Athena"
TARGET_ALIASES = ["Café Athena", "Cafe Athena", "Athena Pacific Beach"]

# Intent tags used to slice KPIs. "value" is the team's Value for money focus.
INTENTS = {"value", "happy_hour", "dietary", "occasion", "date_night",
           "cuisine", "location", "group_events"}

VALUE_TERMS = ["good value", "great value", "affordable", "reasonably priced",
               "reasonable prices", "budget", "inexpensive", "cheap", "bang for",
               "worth the price", "deal", "happy hour", "won't break the bank"]
PRICEY_TERMS = ["expensive", "pricey", "splurge", "high-end", "upscale",
                "overpriced", "special occasion"]


# ---------------------------------------------------------------------------
# Data contract
# ---------------------------------------------------------------------------

@dataclass
class Observation:
    run_id: str
    query_id: str
    query_text: str
    target: str
    model_id: str
    mode: str
    raw_answer: str
    timestamp: str  # ISO 8601


@dataclass
class Fact:
    """One verified restaurant fact from the research facts sheet."""
    field: str
    value: object
    source_url: str


@dataclass
class Proposal:
    target: str
    jsonld: dict
    faq: list[dict] = field(default_factory=list)   # [{"q","a","source_url"}]
    about: str = ""
    about_source_urls: list[str] = field(default_factory=list)
    owner_approved: bool = False
    approved_by: str = ""


class ComplianceError(Exception):
    pass


# Starter facts sheet taken from the brief. Content may only use these.
CAFE_ATHENA_FACTS = [
    Fact("name", "Café Athena", BRIEF_URL),
    Fact("url", "https://cafeathena.com/", BRIEF_URL),
    Fact("servesCuisine", ["Greek", "Mediterranean"], BRIEF_URL),
    Fact("address", {"@type": "PostalAddress", "streetAddress": "1846 Garnet Ave",
                     "addressLocality": "San Diego", "addressRegion": "CA",
                     "postalCode": "92109", "addressCountry": "US"}, BRIEF_URL),
    Fact("openingHours", ["Su-Th 11:00-21:00", "Fr-Sa 11:00-21:30"], BRIEF_URL),
    Fact("foundingDate", "1990", BRIEF_URL),
    # Facts for FAQ and copy only (not schema.org keys):
    Fact("happyHour", "Daily happy hour 3:00 to 6:00 PM with tapas-style small plates", BRIEF_URL),
    Fact("dietaryOptions", "Vegan, vegetarian and gluten-free options", BRIEF_URL),
    Fact("bar", "Full bar with Greek-mythology-themed cocktails", BRIEF_URL),
    Fact("events", "Catering, group dining and private events", BRIEF_URL),
    Fact("neighborhood", "Pacific Beach, San Diego", BRIEF_URL),
]
NON_SCHEMA_FACTS = {"happyHour", "dietaryOptions", "bar", "events", "neighborhood"}


def load_query_bank(path: str | Path) -> list[dict]:
    """Query bank JSON: [{"query_id","query_text","intents":[...]}]. Enforces size and tags."""
    bank = json.loads(Path(path).read_text(encoding="utf-8"))
    if len(bank) != QUERY_BANK_SIZE or len({q["query_id"] for q in bank}) != QUERY_BANK_SIZE:
        raise ComplianceError(f"Query bank must have {QUERY_BANK_SIZE} unique queries.")
    for q in bank:
        bad = set(q.get("intents", [])) - INTENTS
        if bad or not q.get("intents"):
            raise ComplianceError(f"{q['query_id']}: missing or unknown intents {sorted(bad)}.")
    return bank


# ---------------------------------------------------------------------------
# Stage 1: discovery run validation
# ---------------------------------------------------------------------------

def query_bank_hash(queries: Iterable[tuple[str, str]]) -> str:
    """Stable fingerprint of a query bank from (query_id, query_text) pairs."""
    canon = "\n".join(f"{qid}\t{text.strip()}" for qid, text in sorted(queries))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def validate_run(obs: list[Observation]) -> list[str]:
    """Return a list of problems. An empty list means the run passes the gate."""
    issues: list[str] = []
    if not obs:
        return ["Run has no observations."]

    ids = [o.query_id for o in obs]
    if len(set(ids)) != QUERY_BANK_SIZE:
        issues.append(f"Run has {len(set(ids))} unique queries; expected {QUERY_BANK_SIZE}.")
    dupes = [q for q, n in Counter(ids).items() if n > 1]
    if dupes:
        issues.append(f"Duplicate query IDs: {', '.join(sorted(dupes))}.")

    for name in ("run_id", "target", "model_id", "mode"):
        values = {getattr(o, name) for o in obs}
        if len(values) != 1:
            issues.append(f"Run mixes more than one {name}: {sorted(map(str, values))}.")

    for o in obs:
        if o.mode not in VALID_MODES:
            issues.append(f"{o.query_id}: mode '{o.mode}' is not one of {sorted(VALID_MODES)}.")
        if not (o.raw_answer or "").strip():
            issues.append(f"{o.query_id}: raw answer is empty.")
        if not (o.model_id or "").strip():
            issues.append(f"{o.query_id}: model ID is missing.")
        try:
            datetime.fromisoformat(o.timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            issues.append(f"{o.query_id}: timestamp '{o.timestamp}' is not ISO 8601.")
    return issues


def mode_label(mode: str) -> str:
    """Text the UI must show next to any result from a run in this mode."""
    if mode not in MODE_LABELS:
        raise ComplianceError(f"Unknown mode '{mode}'.")
    return MODE_LABELS[mode]


# ---------------------------------------------------------------------------
# Stage 2: analysis (KPIs tied to evidence)
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("&", "and")
    return re.sub(r"\s+", " ", s)


def mentions(text: str, aliases: Iterable[str]) -> bool:
    t = _norm(text)
    return any(re.search(r"(?<![a-z0-9])" + re.escape(_norm(a)) + r"(?![a-z0-9])", t)
               for a in aliases if a.strip())


_RANK_LINE = re.compile(r"^\s*(?:#\s*)?(\d{1,2})\s*[.):]\s+(.*)$")


def explicit_rank(text: str, aliases: Iterable[str]) -> int | None:
    """Rank only when the answer is an explicit numbered list ("1. X", "#2 X", "3) X")."""
    aliases = list(aliases)
    for line in text.splitlines():
        m = _RANK_LINE.match(line)
        if m and mentions(m.group(2), aliases):
            return int(m.group(1))
    return None


def compute_kpis(obs: list[Observation], target_aliases: list[str],
                 competitors: dict[str, list[str]]) -> dict:
    """
    mention_rate : share of queries whose answer names the target.
    top3_rate    : share of queries where the target is explicitly ranked 1 to 3.
    avg_rank     : mean explicit rank over queries where the target was ranked
                   (None if never ranked).
    competitor_counts : number of answers that name each competitor.
    evidence     : per-query detail so every number traces to a query ID.
    Denominator for both rates is the number of queries in the run.
    """
    n = len(obs)
    if n == 0:
        raise ComplianceError("Cannot compute KPIs on an empty run.")
    evidence, ranks = [], []
    mentioned = top3 = 0
    comp = Counter()
    for o in obs:
        hit = mentions(o.raw_answer, target_aliases)
        rank = explicit_rank(o.raw_answer, target_aliases) if hit else None
        mentioned += hit
        if rank is not None:
            ranks.append(rank)
            top3 += rank <= 3
        named = [c for c, al in competitors.items() if mentions(o.raw_answer, al)]
        comp.update(named)
        evidence.append({"query_id": o.query_id, "mentioned": hit, "rank": rank,
                         "competitors": named})
    return {
        "n_queries": n,
        "mention_rate": round(mentioned / n, 4),
        "top3_rate": round(top3 / n, 4),
        "avg_rank": round(sum(ranks) / len(ranks), 2) if ranks else None,
        "competitor_counts": dict(comp.most_common()),
        "evidence": evidence,
    }


def share_of_voice(obs: list[Observation], target_aliases: list[str],
                   competitors: dict[str, list[str]]) -> dict:
    """Each restaurant's share of all restaurant mentions across the run (judging: share-of-voice)."""
    counts = Counter()
    for o in obs:
        if mentions(o.raw_answer, target_aliases):
            counts["__target__"] += 1
        for c, al in competitors.items():
            if mentions(o.raw_answer, al):
                counts[c] += 1
    total = sum(counts.values())
    return {k: round(v / total, 4) for k, v in counts.most_common()} if total else {}


def segment_kpis(obs: list[Observation], bank: list[dict], intent: str,
                 target_aliases: list[str], competitors: dict[str, list[str]]) -> dict:
    """KPIs for only the queries tagged with one intent, e.g. 'value'."""
    ids = {q["query_id"] for q in bank if intent in q.get("intents", [])}
    subset = [o for o in obs if o.query_id in ids]
    if not subset:
        raise ComplianceError(f"No queries tagged '{intent}' in this run.")
    k = compute_kpis(subset, target_aliases, competitors)
    k["intent"] = intent
    k["share_of_voice"] = share_of_voice(subset, target_aliases, competitors)
    return k


def _sentences_with(text: str, aliases: Iterable[str]) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p for p in parts if mentions(p, aliases)]


def value_perception(text: str, aliases: Iterable[str]) -> str:
    """
    How an answer frames the target on price, from the sentences that name it:
    'good_value', 'pricey', 'mixed', 'not_discussed', or 'not_mentioned'.
    Keyword based so it is transparent to judges; the AI role can swap in an LLM label.
    """
    aliases = list(aliases)
    sents = _sentences_with(text, aliases)
    if not sents:
        return "not_mentioned"
    blob = _norm(" ".join(sents))
    v = any(t in blob for t in VALUE_TERMS)
    p = any(t in blob for t in PRICEY_TERMS)
    return "mixed" if v and p else "good_value" if v else "pricey" if p else "not_discussed"


def value_report(obs: list[Observation], bank: list[dict], target_aliases: list[str],
                 competitors: dict[str, list[str]]) -> dict:
    """Value for money lens: perception split, value-query KPIs, and who wins value queries."""
    labels = Counter(value_perception(o.raw_answer, target_aliases) for o in obs)
    value_ids = {q["query_id"] for q in bank if "value" in q.get("intents", [])}
    gaps = []
    for o in obs:
        if o.query_id in value_ids and not mentions(o.raw_answer, target_aliases):
            winners = [c for c, al in competitors.items() if mentions(o.raw_answer, al)]
            gaps.append({"query_id": o.query_id, "query_text": o.query_text, "competitors_named": winners})
    return {
        "perception": dict(labels),
        "value_kpis": segment_kpis(obs, bank, "value", target_aliases, competitors),
        "value_gaps": gaps,
    }


# ---------------------------------------------------------------------------
# Stage 3: action (sourced claims, owner approval, local export only)
# ---------------------------------------------------------------------------

def validate_proposal(p: Proposal, facts: list[Fact]) -> list[str]:
    issues: list[str] = []
    verified = {f.field: f for f in facts}

    for f in facts:
        if not str(f.source_url).startswith(("http://", "https://")):
            issues.append(f"Fact '{f.field}' has no source URL.")

    missing = JSONLD_REQUIRED - p.jsonld.keys()
    if missing:
        issues.append(f"JSON-LD missing required keys: {sorted(missing)}.")
    if p.jsonld.get("@type") not in ("Restaurant", "FoodEstablishment"):
        issues.append("JSON-LD @type should be Restaurant or FoodEstablishment.")

    for key, value in p.jsonld.items():
        if key.startswith("@"):
            continue
        if key in NON_SCHEMA_FACTS:
            issues.append(f"'{key}' is not a schema.org property; use it in FAQ or copy instead.")
            continue
        fact = verified.get(key)
        if fact is None:
            tag = "High-risk claim" if key in HIGH_RISK_FIELDS else "Claim"
            issues.append(f"{tag} '{key}' is not in the verified facts sheet.")
        elif fact.value != value:
            issues.append(f"JSON-LD '{key}' = {value!r} does not match verified fact {fact.value!r}.")

    for i, item in enumerate(p.faq, 1):
        if not str(item.get("source_url", "")).startswith(("http://", "https://")):
            issues.append(f"FAQ #{i} has no source URL.")
    if p.about.strip() and not p.about_source_urls:
        issues.append("About text has no source URLs.")
    return issues


def build_restaurant_jsonld(facts: list[Fact]) -> dict:
    """Restaurant JSON-LD built only from verified schema.org facts."""
    doc = {"@context": "https://schema.org", "@type": "Restaurant"}
    for f in facts:
        if f.field not in NON_SCHEMA_FACTS:
            doc[f.field] = f.value
    return doc


# Content Creator: each gap query maps to an FAQ built only from verified facts.
FAQ_TEMPLATES = {
    "happy_hour": ("Does Café Athena have a happy hour?", ["happyHour"]),
    "value": ("Is there an affordable way to eat at Café Athena in Pacific Beach?", ["happyHour"]),
    "dietary": ("Does Café Athena have vegan, vegetarian or gluten-free options?", ["dietaryOptions"]),
    "group_events": ("Can Café Athena host a private event or group dinner?", ["events"]),
    "occasion": ("Can Café Athena host a private event or group dinner?", ["events"]),
    "date_night": ("Is Café Athena good for a date night in Pacific Beach?", ["bar", "neighborhood"]),
    "cuisine": ("What kind of food does Café Athena serve?", ["servesCuisine", "foundingDate"]),
    "location": ("Where is Café Athena and when is it open?", ["address", "openingHours"]),
}


def _fact_text(f: Fact) -> str:
    v = f.value
    if f.field == "address":
        return f"{v['streetAddress']}, {v['addressLocality']}, {v['addressRegion']} {v['postalCode']}"
    if f.field == "openingHours":
        return "Open Sunday to Thursday 11 AM to 9 PM, Friday and Saturday 11 AM to 9:30 PM"
    if f.field == "servesCuisine":
        return " and ".join(v) + " cuisine"
    if f.field == "foundingDate":
        return f"Serving Pacific Beach since {v}"
    return str(v)


def draft_faq_for_gaps(gap_intents: Iterable[str], facts: list[Fact]) -> list[dict]:
    """One FAQ per gap intent, answer assembled only from facts, with each source URL."""
    by = {f.field: f for f in facts}
    seen, out = set(), []
    for intent in gap_intents:
        if intent not in FAQ_TEMPLATES:
            continue
        q, fields = FAQ_TEMPLATES[intent]
        if q in seen or not all(fl in by for fl in fields):
            continue
        seen.add(q)
        used = [by[fl] for fl in fields]
        out.append({"q": q, "a": ". ".join(_fact_text(f) for f in used) + ".",
                    "source_url": used[0].source_url,
                    "sources": sorted({f.source_url for f in used}), "intent": intent})
    return out


def build_faq_jsonld(faq: list[dict]) -> dict:
    return {"@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": i["q"],
                            "acceptedAnswer": {"@type": "Answer", "text": i["a"]}} for i in faq]}


def readiness_scorecard(p: Proposal, facts: list[Fact], gap_intents: Iterable[str]) -> dict:
    """What the owner sees before approving: coverage, sourcing, open issues."""
    gaps = set(gap_intents)
    covered = {i.get("intent") for i in p.faq} & gaps
    issues = validate_proposal(p, facts)
    sourced = sum(1 for i in p.faq if str(i.get("source_url", "")).startswith("http"))
    return {"gap_intents": sorted(gaps), "covered": sorted(covered),
            "coverage": round(len(covered) / len(gaps), 2) if gaps else 1.0,
            "faq_sourced": f"{sourced}/{len(p.faq)}", "issues": issues,
            "ready_for_approval": not issues}


def export_package(p: Proposal, facts: list[Fact], out_dir: str | Path) -> Path:
    """Write the approved package to a LOCAL folder. Never touches a website."""
    if str(out_dir).lower().startswith(("http://", "https://", "ftp://", "sftp://")):
        raise ComplianceError("Export is local only. No restaurant website is changed.")
    if not p.owner_approved or not p.approved_by.strip():
        raise ComplianceError("Owner approval (checkbox and approver name) is required before export.")
    issues = validate_proposal(p, facts)
    if issues:
        raise ComplianceError("Proposal is not compliant:\n- " + "\n- ".join(issues))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "restaurant.jsonld").write_text(json.dumps(p.jsonld, indent=2), encoding="utf-8")
    (out / "faq.json").write_text(json.dumps(p.faq, indent=2), encoding="utf-8")
    if p.faq:
        (out / "faq_page.jsonld").write_text(json.dumps(build_faq_jsonld(p.faq), indent=2), encoding="utf-8")
    (out / "about.txt").write_text(p.about, encoding="utf-8")
    (out / "approval.json").write_text(json.dumps({
        "target": p.target, "approved_by": p.approved_by,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
    }, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Stage 4: measurement (comparability + caveat)
# ---------------------------------------------------------------------------

def run_signature(obs: list[Observation]) -> dict:
    return {
        "query_bank": query_bank_hash((o.query_id, o.query_text) for o in obs),
        "target": obs[0].target,
        "model_id": obs[0].model_id,
        "mode": obs[0].mode,
    }


def check_comparable(a: list[Observation], b: list[Observation]) -> tuple[bool, list[str]]:
    sa, sb = run_signature(a), run_signature(b)
    reasons = [f"{k} differs: {sa[k]} vs {sb[k]}" for k in sa if sa[k] != sb[k]]
    return (not reasons, reasons)


def compare_runs(a, b, target_aliases, competitors) -> dict:
    ok, reasons = check_comparable(a, b)
    if not ok:
        raise ComplianceError("Runs are not comparable: " + "; ".join(reasons))
    ka, kb = (compute_kpis(r, target_aliases, competitors) for r in (a, b))
    delta = {k: (None if ka[k] is None or kb[k] is None else round(kb[k] - ka[k], 4))
             for k in ("mention_rate", "top3_rate", "avg_rank")}
    return {"before": ka, "after": kb, "delta": delta, "caveat": CAUSALITY_CAVEAT,
            "mode_label": mode_label(a[0].mode)}
