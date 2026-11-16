"""Generate a small synthetic dataset modeled on the Dr.Tracker GI demo
(IBD flare, prednisone 40 mg -> 0 over 8 weeks, -5 mg weekly).

Text is written FROM known ground-truth facts, so every report comes with gold
labels for free. That lets us measure the LLM parser instead of eyeballing it.
All patients are fictional. Seeded, so output is reproducible.

Writes: data/patients.json, data/reports.jsonl, data/gold.jsonl
"""
import json
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

DATA = Path(__file__).parent / "data"
START = date(2026, 9, 28)              # taper day 1 (a Monday)
WEEKS = 6                              # weeks of reports
TAPER_MG = [40, 35, 30, 25, 20, 15, 10, 5]  # full 8-week plan
REPORT_DAYS = [0, 2, 5]                # Mon, Wed, Sat check-ins
WORDS = {1: "once", 2: "twice", 3: "three times", 4: "four times", 5: "five times",
         6: "six times", 7: "seven times", 8: "eight times", 9: "nine times"}

# Weekly ground-truth trajectory per patient (index = week - 1).
PATIENTS = {
    "P1": dict(name="Jordan Lee", story="responder: steady improvement through the taper",
               stools=[7, 6, 5, 4, 3, 2], bristol=[7, 6, 6, 4, 4, 4],
               blood=[1, 1, .5, 0, 0, 0], urgency=[1, 1, .5, 0, 0, 0], nocturnal=[1, .5, 0, 0, 0, 0],
               cramping=[2, 2, 1, 1, 0, 0], side=["trouble_sleeping"] * 3 + [None] * 3, vague=0.0),
    "P2": dict(name="Priya Shah", story="taper flare: improves, then worsens after the 25 -> 20 mg step",
               stools=[6, 5, 3, 2, 5, 7], bristol=[6, 6, 4, 4, 6, 7],
               blood=[1, .5, 0, 0, 1, 1], urgency=[1, .5, 0, 0, 1, 1], nocturnal=[.5, 0, 0, 0, 1, 1],
               cramping=[2, 1, 0, 0, 2, 3], side=["increased_appetite"] * 4 + ["mood_change"] * 2, vague=0.0),
    "P3": dict(name="Sam Rivera", story="vague reporter: hedges, misses check-ins, one contradiction",
               stools=[5, 5, 4, 4, 4, 3], bristol=[6, 6, None, 6, None, 4],
               blood=[.5, .5, .5, 0, 0, 0], urgency=[.5, .5, 0, .5, 0, 0], nocturnal=[0, 0, 0, 0, 0, 0],
               cramping=[1, 1, 1, 1, 0, 0], side=["mood_change", None, None, "trouble_sleeping", None, None], vague=0.5),
}

PHRASES = {
    "bristol": {7: ["all watery", "basically liquid"], 6: ["loose and mushy", "still mushy"],
                4: ["more formed, normal shape", "finally normal and formed"]},
    "blood_yes": ["saw blood again", "some blood in it", "bright red blood on the paper"],
    "blood_no": ["no blood today", "no blood that I could see"],
    "blood_hedged": ["I think there was a little blood?", "maybe a bit of blood, hard to tell"],
    "urgency": ["had to rush, really urgent", "urgency was bad", "barely made it to the bathroom"],
    "nocturnal": ["woke me up last night to go", "had to get up in the night for the bathroom"],
    "cramping": {1: ["a little crampy", "mild cramps"], 2: ["cramping pretty bad around my belly button",
                 "moderate cramps in the lower left"], 3: ["awful cramps, worst in a while", "severe cramping all day"]},
    "no_cramping": ["no cramps today"],
    "trouble_sleeping": ["can't sleep on this prednisone", "up till 3am, couldn't sleep"],
    "increased_appetite": ["starving all the time", "eating everything in sight"],
    "mood_change": ["feeling really irritable", "moody and snappy today"],
    "missed_dose": ["forgot my prednisone this morning", "skipped my pill yesterday"],
}


def gold(symptom, present=True, count=None, bristol=None, uncertain=False):
    return dict(symptom=symptom, present=present, count=count, bristol_type=bristol, uncertain=uncertain)


def build_report(rng: random.Random, p: dict, week: int):
    """Return (text, gold_events) for one check-in."""
    w = week - 1
    frags, labels = [], []

    # Stool: count (exact or vague) + optional Bristol description
    n = max(1, p["stools"][w] + rng.choice([-1, 0, 0, 1]))
    bristol = p["bristol"][w]
    if rng.random() < p["vague"]:
        part, count = rng.choice(["went a few times", "a bunch of bathroom trips", "several BMs"]), None
    else:
        part, count = rng.choice([f"{n} BMs today", f"went {WORDS[n]}", f"{n} trips to the bathroom"]), n
    if bristol:
        part += ", " + rng.choice(PHRASES["bristol"][bristol])
    frags.append(part)
    labels.append(gold("stool", count=count, bristol=bristol))

    # Blood: present / explicitly absent / hedged
    if rng.random() < p["blood"][w]:
        if p["vague"] and rng.random() < 0.6:
            frags.append(rng.choice(PHRASES["blood_hedged"]))
            labels.append(gold("blood_in_stool", uncertain=True))
        else:
            frags.append(rng.choice(PHRASES["blood_yes"]))
            labels.append(gold("blood_in_stool"))
    elif rng.random() < 0.5:
        frags.append(rng.choice(PHRASES["blood_no"]))
        labels.append(gold("blood_in_stool", present=False))

    for sym in ("urgency", "nocturnal"):
        if rng.random() < p[sym][w]:
            frags.append(rng.choice(PHRASES[sym]))
            labels.append(gold(sym))

    sev = p["cramping"][w]
    if sev:
        frags.append(rng.choice(PHRASES["cramping"][sev]))
        labels.append(gold("cramping"))
    elif rng.random() < 0.3:
        frags.append(rng.choice(PHRASES["no_cramping"]))
        labels.append(gold("cramping", present=False))

    side = p["side"][w]
    if side and rng.random() < 0.7:
        frags.append(rng.choice(PHRASES[side]))
        labels.append(gold(side))

    rest = frags[1:]
    rng.shuffle(rest)
    return ". ".join(f[0].upper() + f[1:] for f in [frags[0]] + rest) + ".", labels


# Hand-written edge cases (report_key -> (text, gold)) that templates can't produce.
EDGE_CASES = {
    ("P3", 4, 0): ("No blood today. Actually wait, there was a little streak on the paper. "
                   "Went 4 times, kinda mushy.",
                   [gold("blood_in_stool", uncertain=True), gold("stool", count=4, bristol=6)]),
    ("P3", 3, 2): ("Forgot my prednisone this morning, took it at lunch instead. Bathroom a few times, not too bad.",
                   [gold("missed_dose"), gold("stool")]),
    ("P2", 5, 2): ("woke me up twice tonight, really urgent, and i think there was blood again. "
                   "5 or 6 BMs, all watery",
                   [gold("nocturnal"), gold("urgency"), gold("blood_in_stool", uncertain=True),
                    gold("stool", bristol=7)]),
    ("P1", 1, 5): ("Honestly about the same as wed. 6 trips to the bathroom, loose and mushy. Cramps mild",
                   [gold("stool", count=6, bristol=6), gold("cramping")]),
}


def main():
    rng = random.Random(42)
    DATA.mkdir(exist_ok=True)
    patients, doses, reports, golds = [], [], [], []

    for pid, p in PATIENTS.items():
        patients.append(dict(id=pid, name=p["name"], diagnosis="Ulcerative colitis flare",
                             start_date=START.isoformat(), story=p["story"]))
        for i, mg in enumerate(TAPER_MG):
            doses.append(dict(patient_id=pid, drug="prednisone", dose_mg=mg,
                              start_date=(START + timedelta(weeks=i)).isoformat(),
                              end_date=(START + timedelta(weeks=i + 1, days=-1)).isoformat()))
        doses.append(dict(patient_id=pid, drug="folic acid", dose_mg=1, start_date=START.isoformat(),
                          end_date=(START + timedelta(weeks=8, days=-1)).isoformat()))

        for week in range(1, WEEKS + 1):
            for dow in REPORT_DAYS:
                key = (pid, week, dow)
                if pid == "P3" and key not in EDGE_CASES and rng.random() < 0.25:
                    continue  # vague reporter also misses check-ins
                text, labels = EDGE_CASES.get(key) or build_report(rng, p, week)
                day = START + timedelta(weeks=week - 1, days=dow)
                ts = datetime.combine(day, time(rng.randint(7, 22), rng.choice([0, 15, 30, 45])))
                rid = f"{pid}-{day.isoformat()}"
                reports.append(dict(id=rid, patient_id=pid, reported_at=ts.isoformat(), raw_text=text))
                golds.append(dict(report_id=rid, events=labels))

    (DATA / "patients.json").write_text(json.dumps({"patients": patients, "doses": doses}, indent=2))
    (DATA / "reports.jsonl").write_text("".join(json.dumps(r) + "\n" for r in reports))
    (DATA / "gold.jsonl").write_text("".join(json.dumps(g) + "\n" for g in golds))
    print(f"Wrote {len(reports)} reports for {len(patients)} patients over {WEEKS} weeks -> {DATA}")


if __name__ == "__main__":
    main()
