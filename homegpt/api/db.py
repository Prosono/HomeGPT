from pathlib import Path
import sqlite3
from datetime import datetime

DB_PATH = Path("/data/homegpt.db")

def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH.as_posix(), check_same_thread=False)

def init_db():
    with _conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            mode TEXT NOT NULL,
            focus TEXT,
            summary TEXT,
            actions_json TEXT
        );

        -- new table: analysis_events
        CREATE TABLE IF NOT EXISTS analysis_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_id INTEGER NOT NULL,         -- FK to your existing row id
            ts TEXT NOT NULL,                     -- ISO timestamp (analysis ts)
            category TEXT NOT NULL,               -- security|comfort|energy|anomalies
            title TEXT,                           -- short label (first sentence/headline)
            body TEXT,                            -- full text for this event
            entity_ids TEXT,                      -- comma-separated entity ids extracted, if any
            UNIQUE(analysis_id, category, title, body)
        );

        -- new table: event_feedback
        CREATE TABLE IF NOT EXISTS event_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,            -- FK to analysis_events
            ts TEXT NOT NULL,
            note TEXT NOT NULL,                   -- user feedback
            kind TEXT DEFAULT 'context',          -- context|dismiss|confirm|custom
            source TEXT DEFAULT 'user'            -- user|system
        );

        -- optional for generic “do X?” offers from the model:
        CREATE TABLE IF NOT EXISTS followup_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            label TEXT NOT NULL,                  -- e.g. "List automations that triggered the limit"
            code TEXT NOT NULL,                   -- machine key, e.g. "list_automations"
            status TEXT DEFAULT 'pending'         -- pending|done|failed
        );

        """)
        c.commit()

        

def add_analysis(mode: str, focus: str, summary: str, actions_json: str):
    ts = datetime.utcnow().isoformat()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO analyses (ts, mode, focus, summary, actions_json) VALUES (?, ?, ?, ?, ?)",
            (ts, mode, focus, summary, actions_json),
        )
        c.commit()
        row_id = cur.lastrowid
        # Return the canonical row shape the UI expects
        return [row_id, ts, mode, focus, summary, actions_json]

def get_analyses(limit: int = 50):
    with _conn() as c:
        cur = c.execute(
            "SELECT id, ts, mode, focus, summary, actions_json FROM analyses ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()

def get_analysis(analysis_id: int):
    with _conn() as c:
        cur = c.execute(
            "SELECT id, ts, mode, focus, summary, actions_json FROM analyses WHERE id = ?",
            (analysis_id,),
        )
        row = cur.fetchone()
        return row if row else None
