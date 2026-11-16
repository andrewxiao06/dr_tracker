"""Free text -> validated symptom events -> SQLite, then score against gold labels.

    python parser.py            # parse with OpenAI (uses cache for already-parsed reports)
    python parser.py --cached   # rebuild DB from data/parsed.jsonl only (no API key, no network)

Validation layers (the AI is never trusted blindly):
  1. Structured output: the model MUST return JSON matching models.ReportExtraction (enums included).
  2. Evidence grounding: every event's quote must appear verbatim in the patient's text,
     and any count must appear inside that quote. Otherwise -> needs_review.
  3. Range checks: out-of-range values are nulled and flagged, never silently stored.
  4. Hedged / contradictory statements are kept (safety bias) but flagged for review;
     hedges and explicit negations are re-checked in code, not left to the model.
  5. Accuracy is measured against gold labels from generate.py.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

import db
from models import ReportExtraction

DATA = Path(__file__).parent / "data"
CACHE = DATA / "parsed.jsonl"
MODEL = "gpt-5-nano"          # cheapest OpenAI model with structured outputs
PROMPT_VERSION = "v2"   # v1 -> v2: Bristol + urgency examples (see README for before/after)

INSTRUCTIONS = """You convert a patient's free-text check-in into structured symptom events.
Context: the patient has an ulcerative colitis (IBD) flare and is on a prednisone taper.

Rules:
- Extract ONLY what the patient explicitly says. Never infer, guess, or add clinical interpretation.
- One event per symptom mentioned. Use the closest symptom from the allowed list:
  stool (bowel movements), blood_in_stool, urgency, nocturnal (woke up at night to use the bathroom),
  cramping, mucus, trouble_sleeping, increased_appetite, mood_change, missed_dose (forgot/skipped/late medication).
- present=false ONLY when the patient explicitly says they did NOT have it (e.g. "no blood today").
- count: only an exact number the patient states (digits or words like "twice"). If vague
  ("a few", "several") or a range ("5 or 6"), count=null. Only set count for stool.
- bristol_type (stool only), only if consistency is described: watery/liquid=7,
  mushy/loose/"kinda mushy"/"still mushy"=6, soft blobs with clear edges=5, normal/formed=4,
  lumpy=3 or 2, hard pellets=1. Otherwise null.
- urgency includes "had to rush", "barely made it to the bathroom", "really urgent".
- nocturnal is ONLY waking up to use the bathroom; not being able to sleep is trouble_sleeping.
- increased_appetite includes "starving", "eating everything"; mood_change includes "irritable", "moody", "snappy".
- severity: 1 mild/a little, 2 moderate/pretty bad, 3 severe/awful/worst. null if not described.
- uncertain=true if the patient hedges ("I think", "maybe", "hard to tell") or contradicts
  themselves about that symptom. If they contradict themselves, report the symptom as present.
- evidence: copy the shortest exact phrase from the text that supports the event, character for character.
"""

# Deterministic cues. The model is asked to flag hedging too, but code double-checks
# because missing a hedge on "blood" is the costly error.
HEDGE = re.compile(r"\b(i think|maybe|might|not sure|hard to tell|i guess|possibly|probably)\b")
CONTRADICTION = re.compile(r"\b(actually|wait|never mind|i mean)\b")
NEGATION = re.compile(r"^(no|not|never|didn't|did not|without|zero)\b")
RANGE = re.compile(r"\b\d+\s*(or|-|to)\s*\d+\b")

NUMBER_WORDS = {"once": 1, "one": 1, "twice": 2, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip(" .,!?\"'")


def extract(client, raw_text: str) -> ReportExtraction:
    """The only place the LLM is called. Swap models/providers here."""
    resp = client.responses.parse(
        model=MODEL,
        reasoning={"effort": "minimal"},
        instructions=INSTRUCTIONS,
        input=raw_text,
        text_format=ReportExtraction,
    )
    return resp.output_parsed


def validate(report_id: str, raw_text: str, extraction: dict) -> list[dict]:
    """Deterministic checks on the model output. Returns DB-ready rows."""
    rows, seen = [], set()
    text = _norm(raw_text)
    for ev in extraction["events"]:
        reasons, notes = [], []   # reasons -> needs human review; notes -> logged deterministic fixes
        evidence = ev["evidence"]
        pos = text.find(_norm(evidence)) if evidence.strip() else -1
        grounded = pos >= 0
        if not grounded:
            reasons.append("evidence not found in patient text")
        # Context = the quote plus the words before it in the SAME sentence ("I think there was blood")
        start = max(text.rfind(".", 0, pos) + 1, pos - 40) if grounded else 0
        context = text[start: pos + len(_norm(evidence))] if grounded else ""
        if not ev["uncertain"] and (HEDGE.search(context) or CONTRADICTION.search(_norm(evidence))):
            ev["uncertain"] = True
        # The patient's own words beat the model: "No blood today" can never be stored as blood.
        if ev["present"] and grounded and NEGATION.search(_norm(evidence)) and not CONTRADICTION.search(_norm(evidence)):
            ev["present"] = False
            notes.append("auto-corrected: model said present, but the quote is an explicit negation")
        if ev["count"] is not None and RANGE.search(evidence.lower()):
            reasons.append(f"patient gave a range; count {ev['count']} removed")
            ev["count"] = None
        if ev["count"] is not None:
            nums = {int(n) for n in re.findall(r"\d+", evidence)}
            nums |= {v for w, v in NUMBER_WORDS.items() if re.search(rf"\b{w}\b", evidence.lower())}
            if ev["count"] not in nums:
                reasons.append(f"count {ev['count']} not stated in evidence")
        for field, lo, hi in (("severity", 0, 3), ("count", 0, 40), ("bristol_type", 1, 7)):
            if ev[field] is not None and not lo <= ev[field] <= hi:
                reasons.append(f"{field}={ev[field]} out of range")
                ev[field] = None
        if ev["uncertain"]:
            reasons.append("patient hedged or contradicted themselves")
        if ev["symptom"] in seen:
            reasons.append("symptom extracted twice in one report")
        seen.add(ev["symptom"])
        rows.append(dict(report_id=report_id, symptom=ev["symptom"], present=int(ev["present"]),
                         severity=ev["severity"], count=ev["count"], bristol_type=ev["bristol_type"],
                         timing=ev["timing"], evidence=evidence, grounded=int(grounded), needs_review=int(bool(reasons)),
                         review_reason="; ".join(reasons + notes) or None))
    return rows


def evaluate(events: list[dict]) -> dict:
    """Compare validated events to gold labels from generate.py."""
    gold = [json.loads(l) for l in (DATA / "gold.jsonl").read_text().splitlines()]
    pred = {}
    for e in events:
        pred.setdefault((e["report_id"], e["symptom"]), e)
    tp = n_gold = n_pred = 0
    count_ok = count_n = bristol_ok = bristol_n = flag_ok = flag_n = present_ok = 0
    for g in gold:
        for ge in g["events"]:
            n_gold += 1
            pe = pred.get((g["report_id"], ge["symptom"]))
            if not pe:
                continue
            tp += 1
            present_ok += pe["present"] == int(ge["present"])
            if ge["symptom"] == "stool":
                count_n += 1
                count_ok += pe["count"] == ge["count"]          # includes "correctly left null"
                bristol_n += 1
                bristol_ok += pe["bristol_type"] == ge["bristol_type"]
            if ge["uncertain"]:
                flag_n += 1
                flag_ok += pe["needs_review"] == 1
    n_pred = len(pred)
    pct = lambda a, b: round(100 * a / b, 1) if b else None
    return {
        "reports": len(gold),
        "symptom_recall_%": pct(tp, n_gold),
        "symptom_precision_%": pct(tp, n_pred),
        "present_vs_absent_correct_%": pct(present_ok, tp),
        "stool_count_exact_%": pct(count_ok, count_n),
        "bristol_type_exact_%": pct(bristol_ok, bristol_n),
        "hedged_items_flagged_for_review_%": pct(flag_ok, flag_n),
        "events_flagged_for_review": sum(e["needs_review"] for e in events),
    }


def _key(text: str) -> str:
    return hashlib.sha256(f"{MODEL}|{PROMPT_VERSION}|{text}".encode()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cached", action="store_true", help="use data/parsed.jsonl only; no API calls")
    args = ap.parse_args()

    meta = json.loads((DATA / "patients.json").read_text())
    reports = [json.loads(l) for l in (DATA / "reports.jsonl").read_text().splitlines()]
    cache = {}
    if CACHE.exists():
        cache = {c["key"]: c for c in map(json.loads, CACHE.read_text().splitlines())}

    todo = [r for r in reports if _key(r["raw_text"]) not in cache]
    if todo and args.cached:
        sys.exit(f"{len(todo)} reports not in cache; run without --cached (needs OPENAI_API_KEY).")
    if todo:
        load_dotenv()
        if not os.getenv("OPENAI_API_KEY"):
            sys.exit("Set OPENAI_API_KEY in .env (see .env.example), or run with --cached.")
        from openai import OpenAI
        client = OpenAI()
        print(f"Parsing {len(todo)} reports with {MODEL}...")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda r: extract(client, r["raw_text"]), todo))
        with CACHE.open("a") as f:
            for r, res in zip(todo, results):
                entry = dict(key=_key(r["raw_text"]), report_id=r["id"], model=MODEL,
                             prompt_version=PROMPT_VERSION, extraction=res.model_dump(mode="json"))
                cache[entry["key"]] = entry
                f.write(json.dumps(entry) + "\n")

    events = []
    for r in reports:
        c = cache[_key(r["raw_text"])]
        r.update(model=c["model"], prompt_version=c["prompt_version"])
        events += validate(r["id"], r["raw_text"], c["extraction"])

    db.build(meta["patients"], meta["doses"], reports, events)
    metrics = evaluate(events)
    (DATA / "eval.json").write_text(json.dumps(metrics, indent=2))
    print(f"Loaded {len(reports)} reports, {len(events)} events -> {db.DB_PATH}")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
