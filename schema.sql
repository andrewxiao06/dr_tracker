-- Dr.Tracker Track B schema: event-based, append-only.
-- Plain SQL that runs on SQLite today and ports to Postgres unchanged.

CREATE TABLE patients (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,          -- fictional
    diagnosis   TEXT NOT NULL,
    start_date  TEXT NOT NULL           -- day 1 of the taper (ISO date)
);

-- Entered by the clinic, NOT parsed from patient text: the dose schedule is
-- the source of truth for "what changed", so an LLM never touches it.
CREATE TABLE medication_doses (
    id          INTEGER PRIMARY KEY,
    patient_id  TEXT NOT NULL REFERENCES patients(id),
    drug        TEXT NOT NULL,
    dose_mg     REAL NOT NULL,
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL           -- inclusive
);

-- Raw patient words are kept forever. If the parser improves, we re-parse.
CREATE TABLE reports (
    id              TEXT PRIMARY KEY,
    patient_id      TEXT NOT NULL REFERENCES patients(id),
    reported_at     TEXT NOT NULL,      -- ISO timestamp
    raw_text        TEXT NOT NULL,
    model           TEXT,               -- which model parsed it
    prompt_version  TEXT                -- which prompt parsed it
);

-- One row per thing the patient said. NULL = not stated; 0 = stated none.
CREATE TABLE symptom_events (
    id            INTEGER PRIMARY KEY,
    report_id     TEXT NOT NULL REFERENCES reports(id),
    symptom       TEXT NOT NULL,
    present       INTEGER NOT NULL CHECK (present IN (0, 1)),
    severity      INTEGER CHECK (severity BETWEEN 0 AND 3),
    count         INTEGER CHECK (count BETWEEN 0 AND 40),
    bristol_type  INTEGER CHECK (bristol_type BETWEEN 1 AND 7),
    timing        TEXT,
    evidence      TEXT NOT NULL,        -- verbatim quote from raw_text
    grounded      INTEGER NOT NULL,     -- 1 if evidence was found verbatim in raw_text
    needs_review  INTEGER NOT NULL DEFAULT 0,
    review_reason TEXT
);

CREATE INDEX idx_reports_patient ON reports(patient_id, reported_at);
CREATE INDEX idx_events_report ON symptom_events(report_id);

-- Daily roll-up. Conflicts (e.g. two reports on one day disagree) are resolved
-- HERE, at read time, with a safety bias (MAX), never by overwriting events.
CREATE VIEW daily_summary AS
SELECT
    r.patient_id,
    date(r.reported_at)                                                       AS day,
    MAX(CASE WHEN e.symptom = 'stool' THEN e.count END)                       AS stools,
    AVG(CASE WHEN e.symptom = 'stool' THEN e.bristol_type END)                AS bristol,
    MAX(CASE WHEN e.symptom = 'blood_in_stool' AND e.present = 1 THEN 1 ELSE 0 END) AS blood,
    MAX(CASE WHEN e.symptom = 'urgency' AND e.present = 1 THEN 1 ELSE 0 END)  AS urgency,
    MAX(CASE WHEN e.symptom = 'nocturnal' AND e.present = 1 THEN 1 ELSE 0 END) AS nocturnal,
    MAX(CASE WHEN e.symptom = 'cramping' THEN e.severity END)                 AS cramping,
    COALESCE(SUM(e.needs_review), 0)                                          AS review_items
FROM reports r
-- Ungrounded events (quote not found in the patient's text) are kept for audit
-- and shown in the review list, but never drive the trend.
LEFT JOIN symptom_events e ON e.report_id = r.id AND e.grounded = 1
GROUP BY r.patient_id, date(r.reported_at);
