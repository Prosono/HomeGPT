import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path("/data/homegpt.db")

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            mode TEXT,
            focus TEXT,
            summary TEXT,
            actions TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_analysis(mode: str, focus: str, summary: str, actions: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO analyses (timestamp, mode, focus, summary, actions) VALUES (?, ?, ?, ?, ?)",
        (datetime.utcnow().isoformat(), mode, focus, summary, actions)
    )
    conn.commit()
    conn.close()

def get_analyses(limit: int = 50):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, timestamp, mode, focus, summary FROM analyses ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_analysis(analysis_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,))
    row = cur.fetchone()
    conn.close()
    return row
