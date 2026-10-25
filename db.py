"""Database layer (SQLite): stores pending leads, every scoring run, each
lead's result, rejected rows and the outcomes sales reps record later."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DB_PATH = os.environ.get("DB_PATH", "leads.db")
LOCAL_TZ = ZoneInfo("Asia/Kolkata")
OUTCOMES = ["Contacted", "Replied", "Meeting", "Won", "Lost"]

LEAD_FIELDS = [
    "lead_id", "contact_name", "role", "company", "company_type", "product",
    "annual_volume_tonnes", "indicative_price_usd_kg", "lead_source", "context",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_leads (
    position INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id TEXT, contact_name TEXT, role TEXT, company TEXT, company_type TEXT,
    product TEXT, annual_volume_tonnes TEXT, indicative_price_usd_kg TEXT,
    lead_source TEXT, context TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    received INTEGER NOT NULL,
    valid_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    ai_used INTEGER NOT NULL,
    ai_provider TEXT,
    ai_model TEXT,
    ai_message TEXT
);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    lead_id TEXT NOT NULL,
    contact_name TEXT, role TEXT, company TEXT, company_type TEXT, product TEXT,
    annual_volume_tonnes REAL, indicative_price_usd_kg REAL,
    lead_source TEXT, context TEXT,
    score INTEGER NOT NULL,
    segment TEXT NOT NULL,
    next_action TEXT NOT NULL,
    action_reasons TEXT,      -- JSON list
    parts TEXT,               -- JSON list: the score breakdown
    ai_signal_count INTEGER,
    ai_dropped TEXT,          -- JSON list
    email_subject TEXT,
    email_body TEXT,
    email_source TEXT,
    email_problems TEXT       -- JSON list
);

CREATE TABLE IF NOT EXISTS rejected (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    lead_id TEXT, company TEXT,
    errors TEXT               -- JSON list
);

CREATE TABLE IF NOT EXISTS outcomes (
    result_id INTEGER PRIMARY KEY REFERENCES results(id),
    outcome TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_results_run ON results(run_id);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_time(iso):
    """'2026-10-25T13:36:44+00:00' -> '25 Oct 2026, 7:06 PM' in India time."""
    try:
        t = datetime.fromisoformat(iso).astimezone(LOCAL_TZ)
    except (TypeError, ValueError):
        return iso or ""
    return t.strftime("%d %b %Y, %I:%M %p").lstrip("0").replace(" 0", " ")


# ---------- Pending leads (the list on the Leads page) ----------

def get_pending():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM pending_leads ORDER BY position").fetchall()
    return [{f: (r[f] or "") for f in LEAD_FIELDS} for r in rows]


def add_pending(lead):
    with connect() as conn:
        conn.execute(
            f"INSERT INTO pending_leads ({', '.join(LEAD_FIELDS)}) VALUES ({', '.join('?' * len(LEAD_FIELDS))})",
            [str(lead.get(f, "")) for f in LEAD_FIELDS],
        )


def replace_pending(leads):
    with connect() as conn:
        conn.execute("DELETE FROM pending_leads")
        conn.executemany(
            f"INSERT INTO pending_leads ({', '.join(LEAD_FIELDS)}) VALUES ({', '.join('?' * len(LEAD_FIELDS))})",
            [[str(l.get(f, "")) for f in LEAD_FIELDS] for l in leads],
        )


def clear_pending():
    with connect() as conn:
        conn.execute("DELETE FROM pending_leads")


# ---------- Saving and loading runs ----------

def save_run(received, scored, rejected, ai_status):
    """Store one scoring run with every result and rejected row. Returns the run ID."""
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO runs (created_at, received, valid_count, rejected_count,
                                 ai_used, ai_provider, ai_model, ai_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (now_utc(), received, len(scored), len(rejected), int(bool(ai_status.get("used"))),
             ai_status.get("provider"), ai_status.get("model"), ai_status.get("message")),
        )
        run_id = cur.lastrowid
        for r in scored:
            lead = r["lead"]
            conn.execute(
                """INSERT INTO results (run_id, lead_id, contact_name, role, company, company_type,
                       product, annual_volume_tonnes, indicative_price_usd_kg, lead_source, context,
                       score, segment, next_action, action_reasons, parts, ai_signal_count, ai_dropped,
                       email_subject, email_body, email_source, email_problems)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, lead["lead_id"], lead["contact_name"], lead["role"], lead["company"],
                 lead["company_type"], lead["product"], lead["annual_volume_tonnes"],
                 lead["indicative_price_usd_kg"], lead["lead_source"], lead["context"],
                 r["score"], r["segment"], r["next_action"], json.dumps(r["action_reasons"]),
                 json.dumps(r["parts"]), sum(1 for p in r["parts"] if p["source"] == "AI"),
                 json.dumps(r["ai_dropped"]), r["email"]["subject"], r["email"]["body"],
                 r["email_source"], json.dumps(r["email_problems"])),
            )
        for item in rejected:
            conn.execute(
                "INSERT INTO rejected (run_id, lead_id, company, errors) VALUES (?, ?, ?, ?)",
                (run_id, item["lead"].get("lead_id"), item["lead"].get("company"), json.dumps(item["errors"])),
            )
    return run_id


def _result_from_row(row, outcome):
    lead = {f: row[f] for f in LEAD_FIELDS}
    return {
        "id": row["id"],
        "lead": lead,
        "score": row["score"],
        "segment": row["segment"],
        "next_action": row["next_action"],
        "action_reasons": json.loads(row["action_reasons"] or "[]"),
        "parts": json.loads(row["parts"] or "[]"),
        "ai_dropped": json.loads(row["ai_dropped"] or "[]"),
        "email": {"subject": row["email_subject"], "body": row["email_body"]},
        "email_source": row["email_source"],
        "email_problems": json.loads(row["email_problems"] or "[]"),
        "outcome": outcome,
    }


def load_run(run_id):
    """Rebuild a run in the same shape the results page uses. None if not found."""
    with connect() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            return None
        rows = conn.execute(
            """SELECT r.*, o.outcome FROM results r
               LEFT JOIN outcomes o ON o.result_id = r.id
               WHERE r.run_id = ? ORDER BY r.id""", (run_id,)).fetchall()
        rej = conn.execute("SELECT * FROM rejected WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    return {
        "id": run["id"],
        "created_at": local_time(run["created_at"]),
        "received": run["received"],
        "results": [_result_from_row(r, r["outcome"]) for r in rows],
        "rejected": [{"lead": {"lead_id": r["lead_id"], "company": r["company"]},
                      "errors": json.loads(r["errors"] or "[]")} for r in rej],
        "ai_status": {"used": bool(run["ai_used"]), "provider": run["ai_provider"],
                      "model": run["ai_model"], "message": run["ai_message"]},
    }


def latest_run_id():
    with connect() as conn:
        row = conn.execute("SELECT MAX(id) AS id FROM runs").fetchone()
    return row["id"]


def list_runs():
    """Every run, newest first, with its segment mix."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT ru.*,
                      SUM(r.segment = 'Hot') AS hot, SUM(r.segment = 'Warm') AS warm,
                      SUM(r.segment = 'Nurture') AS nurture, SUM(r.segment = 'Low') AS low,
                      ROUND(AVG(r.score), 0) AS avg_score
               FROM runs ru LEFT JOIN results r ON r.run_id = ru.id
               GROUP BY ru.id ORDER BY ru.id DESC""").fetchall()
    runs = []
    for row in rows:
        d = dict(row)
        d["created_at"] = local_time(d["created_at"])
        for k in ("hot", "warm", "nurture", "low"):
            d[k] = d[k] or 0
        runs.append(d)
    return runs


def run_id_for_result(result_id):
    with connect() as conn:
        row = conn.execute("SELECT run_id FROM results WHERE id = ?", (result_id,)).fetchone()
    return row["run_id"] if row else None


def set_outcome(result_id, outcome):
    if outcome not in OUTCOMES:
        raise ValueError(f"Outcome must be one of {OUTCOMES}")
    with connect() as conn:
        conn.execute(
            """INSERT INTO outcomes (result_id, outcome, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(result_id) DO UPDATE SET outcome = excluded.outcome,
                                                    updated_at = excluded.updated_at""",
            (result_id, outcome, now_utc()),
        )


def clear_outcome(result_id):
    with connect() as conn:
        conn.execute("DELETE FROM outcomes WHERE result_id = ?", (result_id,))
