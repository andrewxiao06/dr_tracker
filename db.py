"""SQLite storage: build the DB from schema.sql and run named queries from queries.sql."""
import re
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "drtracker.db"


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    return sqlite3.connect(path)


def build(patients, doses, reports, events, path: Path = DB_PATH) -> None:
    """Rebuild the database from scratch (idempotent)."""
    path.unlink(missing_ok=True)
    with connect(path) as con:
        con.executescript((ROOT / "schema.sql").read_text())
        con.executemany("INSERT INTO patients VALUES (:id, :name, :diagnosis, :start_date)", patients)
        con.executemany(
            "INSERT INTO medication_doses (patient_id, drug, dose_mg, start_date, end_date) "
            "VALUES (:patient_id, :drug, :dose_mg, :start_date, :end_date)", doses)
        con.executemany(
            "INSERT INTO reports VALUES (:id, :patient_id, :reported_at, :raw_text, :model, :prompt_version)", reports)
        con.executemany(
            "INSERT INTO symptom_events (report_id, symptom, present, severity, count, bristol_type, timing, "
            "evidence, grounded, needs_review, review_reason) VALUES (:report_id, :symptom, :present, :severity, "
            ":count, :bristol_type, :timing, :evidence, :grounded, :needs_review, :review_reason)", events)


def _load_queries() -> dict[str, str]:
    text = (ROOT / "queries.sql").read_text()
    blocks = re.split(r"^-- name: (\w+)\s*$", text, flags=re.M)
    return {blocks[i]: blocks[i + 1].strip() for i in range(1, len(blocks), 2)}


QUERIES = _load_queries()


def query(name: str, path: Path = DB_PATH, **params) -> pd.DataFrame:
    with connect(path) as con:
        return pd.read_sql_query(QUERIES[name], con, params=params)
