# Dr.Tracker · Track B: Patient-Reported Data (Store, Manage & Chart)

**In plain English:** Patients on a steroid taper text in how their gut is doing ("went 5 times, loose, saw some blood"). This prototype turns those messages into structured data, stores it over weeks, and shows a GI provider **one screen** that answers *"Is the taper working, and did anything get worse when we lowered the dose?"* in under 30 seconds. Every number on the screen can be traced back to the patient's exact words, and anything uncertain is flagged for a human to check.

Scenario: modeled on the public Dr.Tracker **GI demo** (IBD flare, prednisone **40 mg → 0 over 8 weeks, −5 mg weekly**, Bristol stool type, blood, urgency, night waking, steroid side effects). All patients and data are **synthetic and fictional**.

```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python parser.py --cached     # rebuild the DB from the committed parse cache (no API key needed)
streamlit run app.py          # provider dashboard
# Full re-run:  python generate.py && python parser.py   (needs OPENAI_API_KEY in .env)
```

## Pipeline

```
generate.py ──► data/reports.jsonl (free text) + data/gold.jsonl (known answers)
                      │
parser.py   ──► LLM extraction (gpt-5-nano, structured output)
                      ├─► deterministic validation (evidence quote, ranges, hedging)
                      ├─► SQLite  data/drtracker.db   (schema.sql)
                      └─► accuracy vs. gold          (data/eval.json)
app.py      ──► reads ONLY via named SQL in queries.sql ──► Streamlit + Plotly
```

| File | Purpose |
|---|---|
| `models.py` | Pydantic extraction schema + controlled symptom vocabulary |
| `schema.sql` | 4 tables + a `daily_summary` view |
| `generate.py` | Seeded synthetic dataset: 3 patients × 6 weeks, 52 reports |
| `parser.py` | LLM call, validation layers, cache, DB load, evaluation |
| `queries.sql` | Named queries used by the dashboard (Q1 weekly trend, Q2 dose-change impact) |
| `app.py` | Provider dashboard |

## Data schema (event-based)

| Table | What it holds | Why |
|---|---|---|
| `patients` | id, fictional name, diagnosis, taper start | |
| `medication_doses` | drug, dose, start/end date | Entered by the clinic, **never parsed by AI**. It is the source of truth for "what changed". |
| `reports` | timestamp, **raw patient text**, model + prompt version | Raw words are never lost, so we can re-parse when the parser improves. |
| `symptom_events` | one row per thing the patient said: symptom, present, severity 0–3, count, Bristol type, timing, **evidence quote**, needs_review + reason | Event rows (not one column per symptom) mean a new symptom or specialty is just a new value. No migration. |

**Vague reports**
- **NULL = "not stated"**, **0 / present=false = "patient said none"**. "No blood today" is real information, but silence is not, and the schema keeps them apart.
- Vague amounts ("a few times", "5 or 6") are stored as `count = NULL`, never a guessed number. The quote is still saved.
- Bristol type is only filled when consistency is described (watery = 7, mushy/loose = 6, formed = 4).
- Severity is a 0–3 scale *loosely inspired by* CTCAE grading. It is not a validated clinical instrument.

**Conflicting reports**
- Events are **append-only**. Nothing is overwritten.
- If a patient contradicts themselves ("No blood… actually wait, there was a streak"), the symptom is recorded as **present** (safety bias) and flagged for review.
- If two check-ins on the same day disagree, the `daily_summary` view resolves it **at read time** with MAX (again a safety bias), and the original events stay intact.

## Parser and how the AI is checked

The LLM (`gpt-5-nano`, the cheapest OpenAI model, about $0.01 for the whole dataset) does **one** job: free text → schema. It is never trusted on its own:

1. **Schema-enforced output.** The model must return JSON matching the Pydantic model, so symptoms come from a fixed list.
2. **Evidence grounding.** Every event must include a quote that appears **verbatim** in the patient's text, and any count must appear in that quote. If not, it goes to `needs_review`. This is the guard against hallucinated fields.
3. **Range checks.** Out-of-range values are nulled and flagged, never silently stored.
4. **Hedging.** "I think there was blood" is kept, but flagged for review.
5. **Measured, not assumed.** The synthetic data is generated *from* known answers, so accuracy is scored automatically:

| Metric (52 reports, ~165 symptom mentions) | v1: model only | v2: + prompt fixes + code checks |
|---|---|---|
| Symptoms found (recall) | 93.1% | **95.4%** |
| Symptoms correct (precision) | 96.4% | **98.2%** |
| Present vs. "said none" correct | 98.1% | **100%** |
| Stool count exact (incl. correctly left blank) | 98.1% | **98.1%** |
| Bristol type exact | 86.5% | **98.1%** |
| Hedged statements flagged for review | **50%** | **100%** |

**What the evaluation caught, and what I changed:**
- **The model flagged only half of the hedges** (it missed "i think there was blood again" and "No blood… actually wait, there was a streak"). Missing a hedge about blood is the costly error, so I added a **deterministic** check for hedge and self-correction words in the same sentence as the quote, instead of relying on the model.
- **The model sometimes stored "No blood today" as blood present.** Now, when the patient's own quote is an explicit negation, code overrides the model and logs an "auto-corrected" note. This happened 5 times.
- **"5 or 6 BMs" was stored as 5.** Ranges now always become `count = NULL` and are flagged.
- **3 events had no supporting quote** (e.g. a "mucus" event the patient never mentioned). They were caught by the grounding check, kept for audit, and **excluded from the trend** (`grounded = 0`).
- Prompt v2 added examples for Bristol ("still mushy" = 6) and urgency.
- **Known remaining gap:** the nano model still misses some paraphrased urgency ("barely made it to the bathroom"). It's a recall miss, not a false value, so the chart under-reports urgency rather than inventing it. Options: a larger model or `reasoning_effort="low"` at a few cents more, or a keyword pre-pass.

Re-run the scoring any time with `python parser.py --cached` (results in `data/eval.json`; the v1 baseline is in `data/eval_v1.json`).

**Where AI is deliberately NOT used:** dose schedules, aggregation, flags/alert rules, and charts are all deterministic SQL/Python. No LLM writes a clinical summary or makes a judgment. Those need to be auditable and repeatable.

## The dashboard: the provider's 30 seconds

- **Flag banner (top):** a simple rule. In the 7 days after a dose step vs. the 7 before, stools/day up ≥50% **or** ≥2 more blood days → "For review". It is worded as a prompt for the clinician, not a diagnosis. Thresholds are placeholders a clinic would set.
- **This week vs. last week:** stools/day, blood days, stool form, and the count of items needing a second look.
- **One chart:** stools/day with red dots on blood days, Bristol type with the typical 3–4 band, and **each week shaded and labeled with the prednisone dose**. The taper flare (Priya Shah, after 25 → 20 mg) is visible at a glance.
- **Symptoms at each dose step** table (Q1), then expandable **"Needs a second look"** and **"check-ins and what was extracted"**, which trace every number back to the patient's words.

## Queries (`queries.sql`)
- **Q1 `weekly_summary`:** per week, the prednisone dose, stools/day, Bristol average, blood/urgency/night-waking days, and items to review.
- **Q2 `dose_change_impact`:** for each prednisone step-down, symptoms in the 7 days before vs. after (joins events to `medication_doses`).

## Privacy and judgment
- Synthetic, fictional data only. No real patient information anywhere.
- The only data sent to the OpenAI API is synthetic text. With real patients this would require a vendor under a BAA or a self-hosted model. This prototype makes **no HIPAA or compliance claim**.
- No clinical claims: flags say "for review", thresholds are illustrative, and the severity scale is not validated.

## Trade-offs
| Choice | Why | Cost / what I'd change |
|---|---|---|
| SQLite | Zero setup, a single file, plain SQL | Postgres for multi-user. `schema.sql` is plain SQL and ports as-is. |
| Template-generated data | Gives a gold set for free, so accuracy is measurable | Less messy than real patient language, so real-world accuracy would be lower. Next step: a hand-labeled set of more varied text. |
| `gpt-5-nano` | Cheapest model; structured output is enough for short texts | Smaller models miss nuance. That's why validation and the review queue exist, and accuracy is reported, not assumed. |
| Rules for flags, not AI | Auditable, repeatable, explainable | Less adaptive. Thresholds should come from clinicians. |
| Free text only | The hardest input; structured questionnaire answers would skip the LLM | Add a `source` column and route questionnaire answers straight to `symptom_events`. |

## How it extends (MVP → product)
- **New symptom or specialty** (oncology, derm): add an enum value in `models.py`. No DB migration.
- **New LLM:** change `extract()` in `parser.py`. `model` and `prompt_version` are stored per report, so old and new parses can be compared.
- **Real-time:** `parser.py` is a batch step today and can run per incoming report from a queue.
- **New views:** the UI reads only through `queries.sql`.

## How each evaluation criterion is addressed
| Criterion | Where |
|---|---|
| Problem understanding | Built for the provider's 30 seconds: "is the taper working, what changed?" Mirrors the real Dr.Tracker GI demo. |
| Data handling | Event schema, raw text kept, NULL vs. 0, append-only conflicts, DB constraints |
| AI proficiency | LLM only for extraction; evidence grounding; measured accuracy; review queue; no AI in clinical logic |
| Communication | Plain-English summary above; plain labels in the dashboard; traceability to patient words |
| Judgment | Synthetic only; no compliance or clinical claims; honest trade-offs above |

## AI usage in building this
I used an AI coding assistant to help write code and boilerplate. I chose the schema, validation rules, flag thresholds, and scenario myself, checked parser output against gold labels, and spot-checked extractions by hand against the raw text.
