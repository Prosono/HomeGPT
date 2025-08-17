# db.py
from pathlib import Path
import sqlite3
from datetime import datetime

DB_PATH = Path("/data/homegpt.db")

def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH.as_posix(), check_same_thread=False)

def init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            mode TEXT NOT NULL,
            focus TEXT,
            summary TEXT,
            actions_json TEXT
        );

        CREATE TABLE IF NOT EXISTS analysis_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            category TEXT NOT NULL,
            title TEXT,
            body TEXT,
            entity_ids TEXT,
            UNIQUE(analysis_id, category, title, body)
        );

        CREATE TABLE IF NOT EXISTS event_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            note TEXT NOT NULL,
            kind TEXT DEFAULT 'context',
            source TEXT DEFAULT 'user'
        );

        CREATE TABLE IF NOT EXISTS followup_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            label TEXT NOT NULL,
            code TEXT NOT NULL,
            status TEXT DEFAULT 'pending'
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
