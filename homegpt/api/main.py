"""
FastAPI application serving the HomeGPT dashboard and API endpoints.

This version introduces these improvements:

1) The `run_analysis` endpoint wraps its core logic in a try/except
   and snapshots/clears the in-memory event buffer under a lock to
   avoid races with the websocket listener.

2) Auto-analysis now uses the *current configured mode* (passive/active)
   rather than hard-coding "passive", and is debounced.

3) The `history` and `get_history_item` endpoints return dictionaries
   with stable keys for the UI. Duplicate /api/history definitions removed.

4) Topology context is fetched automatically and included in passive runs.
"""
# ── Standard Library ────────────────────────────────────────────────────────────
import asyncio
import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional

from zoneinfo import ZoneInfo

# ── Third-Party ────────────────────────────────────────────────────────────────
import requests
import yaml
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from typing import Optional, Dict, Any
import json, time, os
import requests
from fastapi import HTTPException

# ── First-Party / Local (project imports go here) ──────────────────────────────
# from . import something

from homegpt.app.topology import (
    fetch_topology_snapshot,
    pack_topology_for_prompt,
    pack_states_for_prompt,
    pack_history_for_prompt,
)

_NOISE_LINES = re.compile(
    r"^(\s*"
    r"(SMARTi Dashboard.*|SMARTi Has .*|category_\d+_.*:|available_power_sensors_part\d+:|"
    r"Home Assistant .*: \d+ .*|Vetle's device Climate React:.*|"
    r"Average .*: unavailable.*|.*Missing Title:.*|.*Missing Subtitle:.*)"
    r")\s*$",
    re.IGNORECASE,
)

TOPO_MAX_CHARS    = 4000   # ≈1000 tokens
STATE_MAX_CHARS   = 6000   # ≈1500 tokens
HISTORY_MAX_CHARS = 9000   # ≈2250 tokens
EVENTS_MAX_CHARS  = 3000   # ≈ 750 tokens
TOTAL_MAX_CHARS   = 26000  # ≈6500 tokens (final guardrail for the whole user prompt)
CONTEXT_MAX_CHARS = 2000   # = 500 token  - User Feedback tokens

# ---------- History Compressor (signal-preserving) ----------

TRUE_STATES = {"on", "open", "unlocked", "detected", "motion", "home", "present"}
FALSE_STATES = {"off", "closed", "locked", "clear", "no_motion", "away", "not_home"}

# ---------------- Global Event Buffer ----------------
EVENT_BUFFER: list[dict] = []
EVENT_BUFFER_MAX = 20000
AUTO_ANALYSIS_EVENT_THRESHOLD = 8000       # auto-run when we reach 8k events
AUTO_ANALYSIS_MIN_INTERVAL_SEC = 60 * 60   # debounce auto-runs (15 min)

# --- Prompt size pressure tracking (for pre-emptive auto analysis) ---
EVENT_BYTES: int = 0                  # approx char budget for "EVENTS:" bullets
EVENT_UNIQUE_IDS: set[str] = set()    # unique entities seen since last snapshot

# Soft ceilings to trigger auto-run early (tweak to taste or move to config)
EVENTS_TRIGGER_CHARS   = int(EVENTS_MAX_CHARS * 1.50)  # e.g. ~2250 chars
EVENTS_TRIGGER_UNIQUE  = 200                            # ~80 distinct entities
AUTO_SIZE_MIN_INTERVAL_SEC = 60 * 60                   # 10 min debounce (size-based)


# History packing defaults (prompt-balance knobs)
DEFAULT_HISTORY_HOURS = 6         # how many hours back to fetch from /history/period
HISTORY_MAX_LINES = 200           # how many lines we keep after packing
STATE_MAX_LINES = 120             # lines for current state block
TOPO_MAX_LINES = 80               # lines for topology block

EVENT_LOCK = asyncio.Lock()
_analysis_in_progress = asyncio.Event()
_analysis_in_progress.clear()
_last_auto_run_ts: float | None = None


# Import DB, models, analyzer
# MODELS
# MODELS
from typing import Optional  # <-- ensure this is available at module scope

try:
    # If your local models module exists, import everything including FeedbackUpdate
    from .models import (
        AnalysisRequest,
        Settings,
        FollowupRunRequest,
        EventFeedbackIn,
        FeedbackUpdate,   # <-- add this
    )
except ImportError:
    try:
        # If running as packaged module
        from homegpt.api.models import (
            AnalysisRequest,
            Settings,
            FollowupRunRequest,
            EventFeedbackIn,
            FeedbackUpdate,  # <-- add this
        )
    except ImportError:
        # Final fallback: define the minimal models here
        from pydantic import BaseModel

        class AnalysisRequest(BaseModel):
            mode: str | None = None
            focus: str | None = None

        class Settings(BaseModel):
            openai_api_key: str | None = None
            model: str | None = None
            mode: str | None = None
            summarize_time: str | None = None
            control_allowlist: list[str] | None = None
            max_actions_per_hour: int | None = None
            dry_run: bool | None = None
            log_level: str | None = None
            language: str | None = None

        class FollowupRunRequest(BaseModel):
            analysis_id: int
            code: str

        class EventFeedbackIn(BaseModel):
            event_id: int | None = None
            analysis_id: int | None = None
            body: str | None = None
            category: str | None = None
            note: str
            kind: str | None = "context"

        class FeedbackUpdate(BaseModel):
            note: str | None = None
            kind: str | None = None  # "context" | "correction" | "preference"

# DB
try:
    from . import db as _db
except ImportError:
    try:
        from homegpt.api import db as _db
    except ImportError:
        import sqlite3, os
        from pathlib import Path
        class _DBFallback:
            @staticmethod
            def _conn():
                path = Path(os.environ.get("HOMEGPT_DB", "/config/homegpt.db"))
                path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(path))
                conn.row_factory = sqlite3.Row
                return conn
            @staticmethod
            def init_db(): ...
            @staticmethod
            def get_analyses(limit: int): return []
            @staticmethod
            def get_analysis(aid: int): return {}
            @staticmethod
            def add_analysis(mode, focus, summary, actions): return {}
        _db = _DBFallback
db = _db  # expose as 'db'


def _ensure_schema():
    """Create/upgrade tables used by feedback & follow-ups."""
    with db._conn() as c:
        # Events extracted from summaries (one row per bullet)
        c.execute("""
        CREATE TABLE IF NOT EXISTS analysis_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            category TEXT,
            title TEXT,
            body TEXT,
            entity_ids TEXT
        );
        """)
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_events_aid_body ON analysis_events(analysis_id, body);")
        c.execute("CREATE INDEX IF NOT EXISTS ix_analysis_events_analysis_id ON analysis_events(analysis_id);")
        c.execute("CREATE INDEX IF NOT EXISTS ix_analysis_events_ts ON analysis_events(ts);")

        # User feedback on events
        c.execute("""
        CREATE TABLE IF NOT EXISTS event_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            note TEXT NOT NULL,
            kind TEXT,
            source TEXT
        );
        """)
        # If the table already existed without 'source', add it (upgrade-in-place)
        try:
            cols = {r[1] for r in c.execute("PRAGMA table_info(event_feedback)").fetchall()}
            if "source" not in cols:
                c.execute("ALTER TABLE event_feedback ADD COLUMN source TEXT;")
        except Exception:
            pass
        c.execute("CREATE INDEX IF NOT EXISTS ix_event_feedback_event_id ON event_feedback(event_id);")

        # Follow-up actions shown as buttons in the modal
        c.execute("""
        CREATE TABLE IF NOT EXISTS followup_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            label TEXT NOT NULL,
            code TEXT NOT NULL,
            status TEXT DEFAULT 'pending'
        );
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_followup_requests_analysis_id ON followup_requests(analysis_id);")

        c.commit()

# analyzer is optional
try:
    from . import analyzer  # or: from homegpt.api import analyzer
except Exception:
    analyzer = None

def _extract_entity_ids(text: str) -> list[str]:
    """Extract Home Assistant entity IDs from a string."""
    pattern = r'\b(?:sensor|switch|light|climate|lock|binary_sensor|device_tracker)\.[a-zA-Z0-9_]+\b'
    return list(dict.fromkeys(re.findall(pattern, text)))

def _extract_events_from_summary(aid: int, ts: str, summary: str):
    """
    Split a GPT summary into per‑category events.
    Each bullet or paragraph under Security/Comfort/Energy/Anomalies becomes its own row.
   """
    summary = _coerce_headings(summary)  # <<< add this
    events = []
    # Split the summary by markdown headings (### Security, etc.)
    blocks = re.split(r'(?im)^###\s+', summary)
    titles = re.findall(r'(?im)^###\s+(.+)$', summary)
    for i, block in enumerate(blocks[1:]):
        heading = (titles[i] or "").strip().lower()
        if   'security'  in heading: cat = 'security'
        elif 'comfort'   in heading: cat = 'comfort'
        elif 'energy'    in heading: cat = 'energy'
        elif 'anomal'    in heading: cat = 'anomalies'
        else:
            continue
        # split on bullets or paragraphs
        parts = re.split(r'(?m)^\s*[-•]\s+|^\s*$', block)
        parts = [p.strip() for p in parts if p.strip()]
        if not parts:
            parts = [block.strip()]
        for p in parts:
            title = p.split('. ')[0][:140]
            ent_ids = ','.join(_extract_entity_ids(p))
            events.append((aid, ts, cat, title, p, ent_ids))
    return events

# Cache the local tz once
_LOCAL_TZ: ZoneInfo | None = None
def _get_local_tz() -> ZoneInfo:
    global _LOCAL_TZ
    if _LOCAL_TZ is not None:
        return _LOCAL_TZ
    tzname = os.environ.get("TZ")
    try:
        _LOCAL_TZ = ZoneInfo(tzname) if tzname else ZoneInfo("UTC")
    except Exception:
        _LOCAL_TZ = ZoneInfo("UTC")
    return _LOCAL_TZ

def _ts_to_local_iso(ts_val) -> str | None:
    """Parse a DB timestamp (naive or tz-aware), treat naive as UTC, return ISO with local offset (+hh:mm)."""
    if not ts_val:
        return None
    s = str(ts_val)
    try:
        # Accept 'Z' or offset; if naive, assume UTC
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(_get_local_tz())
        return dt.replace(microsecond=0).isoformat()  # e.g., 2025-08-11T14:33:00+02:00
    except Exception:
        # If parsing fails, return as-is
        return s

# Import HA + OpenAI clients and policies
try:
    from homegpt.app.ha import HAClient
    from homegpt.app.openai_client import OpenAIClient
    from homegpt.app.policy import SYSTEM_PASSIVE, SYSTEM_ACTIVE, ACTIONS_JSON_SCHEMA
    HAVE_REAL = True
except Exception:
    HAVE_REAL = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("HomeGPT")

CONFIG_PATH = Path("/config/homegpt_config.yaml")
FRONTEND_DIR = Path(__file__).parent / "frontend"

app = FastAPI(title="HomeGPT Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()
_ensure_schema()  # ← add this

# ---------------- Ingress UI ----------------
@app.get("/")
async def ingress_root():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"error": "Dashboard frontend not found"}

app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")

# ---------------- API Routes ----------------
@app.get("/api/status")
def get_status():
    cfg = _load_config()
    last = db.get_analyses(1)

    # Count events waiting in memory (cheap read; races are fine for display)
    event_count = len(EVENT_BUFFER)

    # Compute seconds since last analysis if possible
    seconds_since_last: float | None = None
    if last:
        row = last[0]
        ts_val = None
        try:
            ts_val = row.get("ts")  # type: ignore[attr-defined]
        except Exception:
            pass
        if not ts_val:
            try:
                ts_val = row[1]
            except Exception:
                ts_val = None
        if ts_val:
            try:
                last_dt = datetime.fromisoformat(str(ts_val))
                now = datetime.now(timezone.utc)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                seconds_since_last = (now - last_dt).total_seconds()
            except Exception:
                seconds_since_last = None

    return {
        "mode": cfg.get("mode", "passive"),
        "model": cfg.get("model", "gpt-5"),
        "dry_run": cfg.get("dry_run", True),
        "last_analysis": last[0] if last else None,
        "event_count": event_count,
        "seconds_since_last": seconds_since_last,
        "history_hours": int(cfg.get("history_hours", DEFAULT_HISTORY_HOURS)),
    }


@app.post("/api/mode")
def set_mode(mode: str = Query(...)):
    logger.info(f"Setting mode to: {mode}")
    cfg = _load_config()
    cfg["mode"] = mode
    _save_config(cfg)
    return {"status": "ok", "mode": mode}


def _coerce_headings(md: str) -> str:
    labels = [
        "Security","Comfort","Energy","Anomalies",
        "Presence","Occupancy","Actions to take","Actions","Next steps"
    ]
    group = "|".join(map(re.escape, labels))
    # turn lines that are just a label (optionally bold/with colon) into ### headings
    pattern = re.compile(rf'(?im)^\s*(?:\*\*|__)?\s*({group})\s*(?:\*\*|__)?\s*:?\s*$')
    return pattern.sub(lambda m: f"### {m.group(1)}", md or "")

async def _fetch_history_all_entities(
    ha,
    all_states: list[dict],
    start_dt,
    end_dt,
    *,
    chunk_size: int,
    minimal_response: bool = True,
) -> list:
    """Fetch history for many entities in manageable chunks, combine results."""
    # Build full entity list from current states
    entity_ids = [s.get("entity_id") for s in all_states if s.get("entity_id")]
    # Safety cap (configurable; defaults below)
    cfg = _load_config()
    max_all = int(cfg.get("history_all_max_entities", 600))
    if len(entity_ids) > max_all:
        entity_ids = entity_ids[:max_all]

    # Chunk
    chunks = [entity_ids[i:i+chunk_size] for i in range(0, len(entity_ids), chunk_size)]
    logger.info("History(all): %d entities in %d chunks (size=%d)", len(entity_ids), len(chunks), chunk_size)

    start = start_dt.isoformat(timespec="seconds")
    end = end_dt.isoformat(timespec="seconds")

    combined: list = []
    for idx, ids in enumerate(chunks, start=1):
        try:
            part = await ha.history_period(
                start, end,
                entity_ids=ids,
                minimal_response=minimal_response,
                include_start_time_state=True,
                significant_changes_only=None,
            )
        except Exception as e:
            logger.warning("Chunk %d/%d failed (min=%s): %s", idx, len(chunks), minimal_response, e)
            # Retry permissive
            try:
                part = await ha.history_period(
                    start, end,
                    entity_ids=ids,
                    minimal_response=False,
                    include_start_time_state=True,
                    significant_changes_only=False,
                )
            except Exception as e2:
                logger.warning("Chunk %d/%d retry failed: %s", idx, len(chunks), e2)
                part = []
        # HA returns a list-of-lists (one list per entity)
        if isinstance(part, list):
            combined.extend(part)

    # Diag
    try:
        groups = len(combined)
        rows = sum(len(g) for g in combined if isinstance(g, list))
        logger.info("History(all) combined: groups=%d total_rows=%d", groups, rows)
    except Exception:
        pass

    return combined

# =========================
# Ask Spectra (LLM Orchestrator)
# =========================
from typing import Optional, Dict, Any
import json, time, os
import requests
from fastapi import HTTPException

# ---- Optional HA API session (works in add-on or with HA_URL/HA_TOKEN) ----
def _ha_api_base_and_headers():
    sup = os.getenv("SUPERVISOR_TOKEN")
    if sup:
        return "http://supervisor/core/api", {"Authorization": f"Bearer {sup}", "Content-Type": "application/json"}
    ha_url, ha_tok = os.getenv("HA_URL"), os.getenv("HA_TOKEN")
    if ha_url and ha_tok:
        return f"{ha_url.rstrip('/')}/api", {"Authorization": f"Bearer {ha_tok}", "Content-Type": "application/json"}
    return None, None

def _http_get(url, headers=None, params=None, timeout=15):
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return r.text
    except Exception as e:
        return {"error": str(e), "url": url}

def _ha_states():
    base, headers = _ha_api_base_and_headers()
    if not base:
        return {"error": "ha_unavailable"}
    return _http_get(f"{base}/states", headers=headers)

def _ha_state(entity_id: str):
    base, headers = _ha_api_base_and_headers()
    if not base:
        return {"error": "ha_unavailable"}
    return _http_get(f"{base}/states/{entity_id}", headers=headers)

def _ha_history(entity_id: str, start_iso: Optional[str] = None, end_iso: Optional[str] = None):
    base, headers = _ha_api_base_and_headers()
    if not base:
        return {"error": "ha_unavailable"}
    params = {"filter_entity_id": entity_id}
    url = f"{base}/history/period/{start_iso}" if start_iso else f"{base}/history/period"
    if end_iso:
        params["end_time"] = end_iso
    return _http_get(url, headers=headers, params=params, timeout=30)

def _ha_search_entities(query: str, domain: Optional[str] = None):
    data = _ha_states()
    if isinstance(data, dict) and data.get("error"):
        return data
    q = (query or "").lower()
    out = []
    for row in data or []:
        eid = row.get("entity_id", "")
        attrs = row.get("attributes", {})
        name = (attrs.get("friendly_name") or "").lower()
        if q in eid.lower() or q in name:
            if domain and not eid.startswith(domain + "."):
                continue
            out.append({
                "entity_id": eid,
                "state": row.get("state"),
                "friendly_name": attrs.get("friendly_name"),
                "device_class": attrs.get("device_class"),
                "area_id": attrs.get("area_id"),
                "unit_of_measurement": attrs.get("unit_of_measurement"),
            })
    return out[:50]

# ---- LLM plumbing (OpenAI tool calling) ----
def _openai_client():
    from openai import OpenAI
    return OpenAI()

# Tool defs (schema-driven; model chooses what to call)
TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "get_analyses",
            "description": "List recent analysis summaries from Spectra.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type":"integer","default":50},
                    "since": {"type":"string","description":"ISO timestamp lower bound (optional)"}
                }
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_events",
            "description": "List observed events saved by Spectra (door open/close, energy spikes, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type":"integer","default":200},
                    "since": {"type":"string"},
                    "entity_id": {"type":"string"},
                    "category": {"type":"string","description":"security, energy, comfort, anomalies"}
                }
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ha_search_entities",
            "description": "Fuzzy search live Home Assistant entities by friendly name or id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type":"string"},
                    "domain": {"type":"string","description":"e.g. light, binary_sensor, climate"}
                },
                "required":["query"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ha_get_state",
            "description": "Get current state of a Home Assistant entity.",
            "parameters": {"type":"object","properties":{"entity_id":{"type":"string"}},"required":["entity_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ha_get_history",
            "description": "Fetch recent state history of an entity.",
            "parameters": {
                "type":"object",
                "properties":{
                    "entity_id":{"type":"string"},
                    "start_iso":{"type":"string"},
                    "end_iso":{"type":"string"}
                },
                "required":["entity_id"]
            },
        },
    },
]

# Reuse your existing route functions for data (no duplicate SQL needed)
def _tool_router(name: str, args: Dict[str, Any]):
    if name == "get_analyses":
        limit = int(args.get("limit") or 50)
        since = args.get("since")
        # call your /api/history logic directly
        return {"rows": get_history(limit=limit)} if since is None else {"rows": get_history(limit=limit, since=since)}
    if name == "get_events":
        limit = int(args.get("limit") or 200)
        since = args.get("since")
        category = args.get("category")
        # call your /api/events logic directly
        return {"rows": get_events(since=since, category=category, limit=limit)}
    if name == "ha_search_entities":
        return {"rows": _ha_search_entities(args.get("query",""), args.get("domain"))}
    if name == "ha_get_state":
        return {"row": _ha_state(args.get("entity_id",""))}
    if name == "ha_get_history":
        return {"rows": _ha_history(args.get("entity_id",""), args.get("start_iso"), args.get("end_iso"))}
    return {"error": f"Unknown tool {name}"}

SPECTRA_SYSTEM_PROMPT = """You are Spectra, a Home Assistant copilot that can answer questions and draft automations.
You can call tools to fetch Spectra analyses, Spectra events, and (when configured) live Home Assistant data.

Rules:
- Decide what information you need, then call tools. You may call multiple tools.
- For timing questions (“how long has the door been open?”) fetch the entity’s current state and/or history and compute the duration.
- For automation requests, return both an explanation and *valid* Home Assistant automation YAML under 'automation_yaml'. Use *real* entity_ids found via tools; if none found, use placeholders like 'light.living_room' and add a 'placeholders' array with suggested candidates.
- Always return a single JSON object: {
  "answer_md": string,
  "entities": string[],
  "links": [{"label":string,"url":string}][],
  "automation_yaml"?: string,
  "placeholders"?: string[]
}
Be concise and practical."""

def _ensure_json_obj(text: str) -> Dict[str, Any]:
    import re
    s = text.strip()
    m = re.search(r"\{[\s\S]*\}\s*$", s)
    if m:
        s = m.group(0)
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {"answer_md": text}
    except Exception:
        return {"answer_md": text}

@app.post("/api/ask")
def ask_spectra(payload: Dict[str, Any]):
    q = (payload or {}).get("q", "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Empty question")

    client = _openai_client()
    messages = [
        {"role":"system","content": SPECTRA_SYSTEM_PROMPT},
        {"role":"user","content": q},
    ]

    # Tool loop
    for _ in range(6):
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL","gpt-5"),
            messages=messages,
            tools=TOOL_DEFS,
            tool_choice="auto",
            #temperature=0.2,
        )
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            return _ensure_json_obj(msg.content or "")

        # Execute tools and feed results
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            result = _tool_router(name, args)
            messages.append({
                "role":"tool",
                "tool_call_id": tc.id,
                "name": name,
                "content": json.dumps(result),
            })
        messages.append({"role":"assistant","content": None,"tool_calls": tool_calls})

    return {"answer_md": "Sorry, I couldn't finish that. Please try again."}

#End SPectra ASk ENd

def _load_context_memos(entity_ids: list[str], category: str):
    out = []
    with db._conn() as c:
        # entity-specific memos
        if entity_ids:
            likes = [f"%{e}%" for e in entity_ids]
            q = "SELECT ef.note FROM event_feedback ef JOIN analysis_events ev ON ev.id=ef.event_id WHERE " + " OR ".join(["ev.entity_ids LIKE ?"]*len(likes)) + " ORDER BY ef.ts DESC LIMIT 10"
            out += [r[0] for r in c.execute(q, likes).fetchall()]
        # generic category memos
        q2 = "SELECT ef.note FROM event_feedback ef JOIN analysis_events ev ON ev.id=ef.event_id WHERE (ev.entity_ids IS NULL OR ev.entity_ids = '') AND ev.category=? ORDER BY ef.ts DESC LIMIT 10"
        out += [r[0] for r in c.execute(q2, (category,)).fetchall()]
    return out


def _save_feedback_generic(payload: dict):
    """
    Accepts both:
      {event_id, note, kind?, source?}
    and legacy / UI forms with {analysis_id, body, category, note, kind?}.
    Resolves/creates an analysis_events row if needed, then inserts feedback.
    """
    note = (payload.get("note") or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="Missing note")

    eid = payload.get("event_id")

    # If no event_id, try to resolve by (analysis_id + body). Create event row if needed.
    if not eid:
        aid = payload.get("analysis_id")
        body = (payload.get("body") or "").strip()
        if not (aid and body):
            raise HTTPException(status_code=400, detail="Missing event_id or (analysis_id+body)")
        with db._conn() as c:
            row = c.execute(
                "SELECT id FROM analysis_events WHERE analysis_id=? AND body=? LIMIT 1",
                (int(aid), body)
            ).fetchone()
            if row:
                eid = int(row[0])
            else:
                ent_ids = ",".join(_extract_entity_ids(body))
                ts = datetime.utcnow().isoformat()
                c.execute(
                    "INSERT INTO analysis_events (analysis_id, ts, category, title, body, entity_ids) VALUES (?,?,?,?,?,?)",
                    (int(aid), ts, (payload.get("category") or "generic"), body[:140], body, ent_ids),
                )
                eid = int(c.lastrowid)
                c.commit()

    ts = datetime.utcnow().isoformat()
    kind = payload.get("kind") or "context"
    source = payload.get("source") or "user"

    # Insert using whatever columns exist (fresh install has both).
    with db._conn() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(event_feedback)").fetchall()}
        params = [int(eid), ts, note]
        if "kind" in cols and "source" in cols:
            c.execute(
                "INSERT INTO event_feedback (event_id, ts, note, kind, source) VALUES (?,?,?,?,?)",
                params + [kind, source],
            )
        elif "kind" in cols:
            c.execute(
                "INSERT INTO event_feedback (event_id, ts, note, kind) VALUES (?,?,?,?)",
                params + [kind],
            )
        else:
            c.execute(
                "INSERT INTO event_feedback (event_id, ts, note) VALUES (?,?,?)",
                params,
            )
        c.commit()
    return {"ok": True}

##----------- COMPRESION start ---------------------------



def strip_noise(text: str) -> str:
    """Drop lines that are UI/config spam and blank lines."""
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        if _NOISE_LINES.match(line):
            continue
        out.append(line)
    return "\n".join(out)

def clamp_chars(text: str, max_chars: int) -> str:
    """Trim on line boundaries to max_chars and annotate if truncated."""
    t = text or ""
    if len(t) <= max_chars:
        return t
    used = 0
    out: list[str] = []
    for line in t.splitlines():
        ln = len(line) + 1
        if used + ln > max_chars:
            break
        out.append(line)
        used += ln
    out.append("… [truncated]")
    return "\n".join(out)

def _extract_followups(aid: int, ts: str, summary: str):
    import re
    # e.g. matches:
    # 1) list automations
    # 2. show energy timeline
    # 3: troubleshoot sensor
    FOLLOWUP_RE = re.compile(r'(?ms)^\s*\(?(\d+)[\)\.\:]\s*(.+?)(?=^\s*\(?\d+[\)\.\:]\s*|\Z)')
    rows = []
    for num, label in FOLLOWUP_RE.findall(summary):
        lbl = " ".join(label.split()).strip().lower()
        if "list" in lbl and "automation" in lbl:
            code = "list_automations"
        elif "timeline" in lbl and ("energy" in lbl or "sensor" in lbl):
            code = "show_energy_timeline"
        elif "troubleshoot" in lbl or "faulty" in lbl:
            code = "troubleshoot_sensor"
        else:
            continue
        rows.append((aid, ts, label.strip()[:255], code))
    return rows

def _build_context_memos_block(entity_ids: list[str]) -> str:
    """
    Pull recent user feedback (memos), prioritizing memos that mention the same
    entity_ids, plus generic memos for each category.
    Returns a small markdown block grouped by category.
    """
    cats = ["security", "comfort", "energy", "anomalies", "presence"]
    sections: list[str] = []

    for cat in cats:
        try:
            memos = _load_context_memos(entity_ids, cat)  # entity-targeted first, then generic
            # keep it tight so it doesn't dominate the prompt
            memos = list(dict.fromkeys([m.strip() for m in memos if m and m.strip()]))[:6]
            if memos:
                body = "\n".join(f"- {m}" for m in memos)
                sections.append(f"### {cat.title()}\n{body}")
        except Exception:
            continue

    block = "\n\n".join(sections)
    return clamp_chars(block, CONTEXT_MAX_CHARS)

def compose_user_prompt(
    *,
    lang: str,
    hours: int | None,
    topo: str,
    state_block: str,
    history_block: str,
    events_block: str = "",
    context_block: str = "",
) -> str:
    topo          = clamp_chars(strip_noise(topo), TOPO_MAX_CHARS)
    state_block   = clamp_chars(strip_noise(state_block), STATE_MAX_CHARS)
    history_block = clamp_chars(history_block, HISTORY_MAX_CHARS)
    events_block  = clamp_chars(events_block, EVENTS_MAX_CHARS) if events_block else ""
    context_block = clamp_chars(context_block, CONTEXT_MAX_CHARS) if context_block else ""

    header = (
        f"Language: {lang}.\n"
        "Use CURRENT STATE as ground truth. Be concise and actionable.\n"
        "If USER CONTEXT MEMOS conflict with weak/ambiguous signals, prefer the memos.\n\n"
    )

    # Order: context (if any) → topology → state → history → events
    body_parts = []
    if context_block:
        body_parts.append("USER CONTEXT MEMOS (from prior feedback):\n" + context_block)
    body_parts.append(topo)
    body_parts.append(state_block)
    body_parts.append(history_block)
    if events_block:
        body_parts.append("EVENTS:\n" + events_block)

    body = "\n\n".join(body_parts)
    return clamp_chars(header + body, TOTAL_MAX_CHARS)


def _parse_iso_aware(s: str) -> datetime:
    # HA returns ISO like '2025-08-11T06:23:10+00:00' or without tz.
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def _try_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(str(x).replace(",", "."))
    except Exception:
        return None

def _domain_of(eid: str) -> str:
    return eid.split(".", 1)[0] if "." in eid else ""

def _is_true_state(s: str) -> bool | None:
    lc = str(s).strip().lower()
    if lc in TRUE_STATES:
        return True
    if lc in FALSE_STATES:
        return False
    return None

def _format_pct(p: float) -> str:
    return f"{p:.0f}%"

def _sec_hm(sec: float) -> str:
    sec = max(0, int(sec))
    h, r = divmod(sec, 3600)
    m, _ = divmod(r, 60)
    if h >= 1:
        return f"{h}h {m}m"
    return f"{m}m"

def _compress_entity_series(series: list[dict], now: datetime, jitter_sec: int = 90) -> tuple[str, float]:
    """
    Returns (one_line_summary, activity_score).
    'activity_score' is used to rank entities globally.
    """
    if not series:
        return ("", 0.0)

    eid = series[0].get("entity_id", "unknown.unknown")
    domain = _domain_of(eid)

    # Extract (ts, state, attrs) sorted, and coalesce jitter
    rows = []
    for row in series:
        st = row.get("state")
        ts = _parse_iso_aware(row.get("last_changed") or row.get("last_updated") or row.get("time_fired", ""))
        rows.append((ts, st, row.get("attributes", {})))
    rows.sort(key=lambda x: x[0])

    # Coalesce successive changes within jitter_sec to reduce noise
    coalesced: list[tuple[datetime, Any, dict]] = []
    for ts, st, attr in rows:
        if coalesced and (ts - coalesced[-1][0]).total_seconds() <= jitter_sec:
            # overwrite last state if within jitter window
            coalesced[-1] = (ts, st, attr)
        else:
            coalesced.append((ts, st, attr))
    rows = coalesced

    # Current/last
    last_ts, last_state, last_attr = rows[-1]
    last_state_str = str(last_state)

    # Attempt numeric path
    vals: list[float] = []
    for _, st, _ in rows:
        v = _try_float(st)
        if v is not None and str(st).lower() not in {"unknown", "unavailable"}:
            vals.append(v)

    # Choose path based on domain/values
    line = ""
    activity = 0.0

    # Binary-ish?
    t_flags = [(_is_true_state(st), ts) for ts, st, _ in rows]
    if any(tf is not None for tf, _ in t_flags) and domain in {"binary_sensor", "lock", "cover", "switch"}:
        # Compute %true and longest true streak
        true_dur = 0.0
        longest_true = 0.0
        last_t = rows[0][0]
        prev_true = t_flags[0][0]
        for (tf, ts), nxt in zip(t_flags, t_flags[1:] + [(None, now)]):
            # duration until next timestamp (or now)
            nxt_ts = nxt[1] if nxt[1] is not None else now
            dur = (nxt_ts - ts).total_seconds()
            if tf is True:
                true_dur += max(0.0, dur)
                longest_true = max(longest_true, max(0.0, dur))
        total = (now - rows[0][0]).total_seconds() or 1.0
        pct = true_dur / total
        last_change_ago = _sec_hm((now - last_ts).total_seconds())

        pretty_state = last_state_str.upper()
        if domain == "lock":
            pretty_state = "UNLOCKED" if _is_true_state(last_state_str) else "LOCKED"
        elif domain == "cover":
            # Many covers are numeric; if not, treat open/closed
            pretty_state = "OPEN" if _is_true_state(last_state_str) else "CLOSED"
        elif domain == "binary_sensor":
            # device_class could refine, but keep generic
            pretty_state = "ON" if _is_true_state(last_state_str) else "OFF"

        line = f"- {eid}: {pretty_state} (true {_format_pct(pct)}, longest {_sec_hm(longest_true)}, last change {last_change_ago} ago)"
        # Activity: number of effective changes per hour
        activity = max(1.0, len(rows) / max(1.0, (now - rows[0][0]).total_seconds() / 3600.0))
        return (line, activity)

    # Numeric path
    if len(vals) >= 2:
        v_now = _try_float(last_state_str)
        v_min = min(vals)
        v_max = max(vals)
        v_mean = mean(vals)
        # count “meaningful” jumps (> 10% of range or absolute 1 unit)
        rng = max(1e-9, v_max - v_min)
        jumps = 0
        prev = vals[0]
        for v in vals[1:]:
            if abs(v - prev) >= max(1.0, 0.10 * rng):
                jumps += 1
            prev = v

        unit = last_attr.get("unit_of_measurement") or ""
        # Energy-ish monotonic delta
        delta_txt = ""
        if "kwh" in unit.lower() or "wh" in unit.lower() or "energy" in eid:
            # approximate delta if monotonic increasing
            delta = vals[-1] - vals[0]
            if abs(delta) > 1e-6:
                delta_txt = f", Δ {delta:.2f}{unit}"

        now_txt = f"{v_now:.2f}{unit}" if v_now is not None else last_state_str
        line = (f"- {eid}: now {now_txt}, min {v_min:.2f}{unit}, max {v_max:.2f}{unit}, "
                f"avg {v_mean:.2f}{unit}, changes {jumps}{delta_txt}")
        activity = max(1.0, jumps)
        return (line, activity)

    # Fallback categorical / texty
    line = f"- {eid}: state={last_state_str} (last change {_sec_hm((now - last_ts).total_seconds())} ago)"
    activity = max(1.0, len(rows) / max(1.0, (now - rows[0][0]).total_seconds() / 3600.0))
    return (line, activity)


def compress_history_for_prompt(hist: list, *, now: datetime | None = None,
                                max_lines: int = 160, jitter_sec: int = 90) -> str:
    """
    Convert HA history (list per entity) into <= max_lines bullet lines,
    ranked by “activity” so we preserve the most informative entities.
    """
    if not hist:
        return "(no history available)"

    now = now or datetime.now(timezone.utc)
    lines: list[tuple[str, float]] = []

    for series in hist:
        if not isinstance(series, list) or not series:
            continue
        # Skip unavailable-only series quickly
        if all((str(r.get("state")).lower() in {"unknown", "unavailable", "none"}) for r in series):
            continue
        try:
            line, score = _compress_entity_series(series, now, jitter_sec=jitter_sec)
            if line:
                lines.append((line, score))
        except Exception:
            # Defensive: never kill the run due to one entity
            continue

    if not lines:
        return "(history had no usable states)"

    # Rank by score (desc) and take top max_lines
    lines.sort(key=lambda x: x[1], reverse=True)
    kept = lines[:max_lines]
    body = "\n".join(l for (l, _) in kept)

    return "HISTORY (compressed):\n" + body

##----------- COMPRESION END ---------------------------

async def _perform_analysis(mode: str, focus: str, trigger: str = "manual"):
    """Shared analysis worker used by manual and auto triggers."""
    cfg = _load_config()
    summary: str = ""
    actions: list = []

    if HAVE_REAL:
        ha = HAClient()
        gpt = OpenAIClient(model=cfg.get("model"))
        try:
            if mode == "passive":
                # ----- Snapshot & clear events -----
                async with EVENT_LOCK:
                    global EVENT_BYTES, EVENT_UNIQUE_IDS
                    events = EVENT_BUFFER[-EVENT_BUFFER_MAX:]
                    EVENT_BUFFER.clear()
                    EVENT_BYTES = 0
                    EVENT_UNIQUE_IDS.clear()

                if not events:
                    return {"summary": "No notable events recorded.", "actions": [], "row": None}

                # ----- Topology -----
                topo = await fetch_topology_snapshot(ha, max_lines=TOPO_MAX_LINES)

                # ----- Current states -----
                all_states = await ha.states()
                state_block = pack_states_for_prompt(all_states, max_lines=STATE_MAX_LINES)

                # ----- History window -----
                cfg_hours = int(cfg.get("history_hours", DEFAULT_HISTORY_HOURS))
                now = datetime.now(timezone.utc).replace(microsecond=0)
                start = (now - timedelta(hours=cfg_hours)).replace(microsecond=0)

                # Prefer recent event entities for history; fall back to a subset of states
                entity_ids = [e["entity_id"] for e in events[-10:] if e.get("entity_id")]
                if not entity_ids:
                    entity_ids = sorted({s.get("entity_id") for s in all_states if s.get("entity_id")})[:300]

                logger.info("History query: %d ids (first 15): %s", len(entity_ids), entity_ids[:15])

                # ----- History fetch (with permissive fallback) -----
                try:
                    hist = await ha.history_period(
                        start.isoformat(timespec="seconds"),
                        now.isoformat(timespec="seconds"),
                        entity_ids=entity_ids,
                        minimal_response=True,
                        include_start_time_state=True,
                        significant_changes_only=None,
                    )
                except Exception as e:
                    logger.warning("History fetch failed (first attempt): %s", e)
                    try:
                        hist = await ha.history_period(
                            start.isoformat(timespec="seconds"),
                            now.isoformat(timespec="seconds"),
                            entity_ids=entity_ids,
                            minimal_response=False,
                            include_start_time_state=True,
                            significant_changes_only=False,
                        )
                    except Exception as e2:
                        logger.warning("History fetch failed (second attempt): %s", e2)
                        hist = []

                # Diagnostics
                try:
                    groups = len(hist) if isinstance(hist, list) else 0
                    rows = sum(len(g) for g in hist if isinstance(g, list))
                    logger.info("History fetched: groups=%d total_rows=%d", groups, rows)
                except Exception:
                    pass

                # ----- History compression -----
                try:
                    history_block = compress_history_for_prompt(
                        hist,
                        now=datetime.now(timezone.utc),
                        max_lines=int(cfg.get("history_max_lines", HISTORY_MAX_LINES)),
                        jitter_sec=int(cfg.get("history_jitter_sec", 90)),
                    )
                except Exception as e:
                    logger.warning("History pack failed: %s", e)
                    history_block = "(history unavailable)"

                # ----- Event bullets (from the snapshot) -----
                bullets = [
                    f"{e['ts']} · {e['entity_id']} : {e['from']} → {e['to']}"
                    for e in events[-AUTO_ANALYSIS_EVENT_THRESHOLD:]
                ]
                events_block = "\n".join(bullets) if bullets else "(none)"

                # ----- USER CONTEXT MEMOS (entity-targeted + generic) -----
                # Use *all* event entity_ids we have to fetch targeted memos
                entity_ids_for_context = sorted({e.get("entity_id") for e in events if e.get("entity_id")})
                try:
                    # Preferred helper, if you added it
                    context_block = _build_context_memos_block(entity_ids_for_context)  # type: ignore[name-defined]
                except NameError:
                    # Fallback inline builder using _load_context_memos
                    try:
                        cats = ["security", "comfort", "energy", "anomalies", "presence"]
                        parts = []
                        for cat in cats:
                            memos = _load_context_memos(entity_ids_for_context, cat)
                            memos = list(dict.fromkeys([m.strip() for m in memos if m and m.strip()]))[:6]
                            if memos:
                                parts.append("### " + cat.title() + "\n" + "\n".join(f"- {m}" for m in memos))
                        block = "\n\n".join(parts)
                        # keep tight; don't let memos dominate the prompt
                        context_block = clamp_chars(block, 2000)
                    except Exception:
                        context_block = ""
                logger.info("Context memos included: %d chars", len(context_block or ""))

                # ----- Compose user prompt (now includes context_block) -----
                user = compose_user_prompt(
                    lang=cfg.get("language", "en"),
                    hours=cfg_hours,
                    topo=topo,
                    state_block=state_block,
                    history_block=history_block,
                    events_block=events_block,
                    context_block=context_block,
                )

                # ----- Model call -----
                summary = gpt.complete_text(SYSTEM_PASSIVE, user)
                actions = []

            else:
                # ----- Active mode (JSON action plan) -----
                states = await ha.states()
                lines = [f"{s['entity_id']}={s['state']}" for s in states[:400]]
                user = f"Mode: {mode}\nCurrent states (subset):\n" + "\n".join(lines)
                plan = gpt.complete_json(SYSTEM_ACTIVE, user, schema=ACTIONS_JSON_SCHEMA)
                summary = plan.get("text") or plan.get("summary") or "No summary."
                actions = plan.get("actions") or []

        finally:
            await ha.close()

    else:
        # Fallback (mocked)
        summary = f"Analysis in {mode} mode. Focus: {focus or 'General'}."
        actions = ["light.turn_off living_room", "climate.set_temperature bedroom 20°C"]

    # ----- Persist -----
    row = db.add_analysis(mode, focus or (f"{trigger} trigger"), summary, json.dumps(actions))

    # ----- Extract events & follow-ups -----
    if isinstance(row, (list, tuple)):
        row_id, row_ts = row[0], row[1]

        # Events
        events = _extract_events_from_summary(row_id, row_ts, summary)
        if events:
            with db._conn() as c:
                c.executemany(
                    "INSERT OR IGNORE INTO analysis_events (analysis_id, ts, category, title, body, entity_ids) VALUES (?,?,?,?,?,?)",
                    events
                )
                c.commit()

        # Follow-ups
        fups = _extract_followups(row_id, row_ts, summary)
        if fups:
            with db._conn() as c:
                c.executemany(
                    "INSERT INTO followup_requests (analysis_id, ts, label, code) VALUES (?,?,?,?)",
                    fups,
                )
                c.commit()

    # ----- Notify -----
    if HAVE_REAL:
        try:
            ha_notify = HAClient()
            title = "HomeGPT – Analysis (auto)" if trigger == "auto" else "HomeGPT – Analysis"
            await ha_notify.notify(title, summary)
        finally:
            try:
                await ha_notify.close()
            except Exception:
                pass

    return {"summary": summary, "actions": actions, "row": row}



#@app.post("/api/feedback")
#def post_feedback_alias(payload: dict = Body(...)):
#    """
#    Compatibility endpoint. Accepts either:
#      {event_id, note, kind?}
#    or legacy: {event_id, feedback, kind?}
#    Also supports {analysis_id, body, category, note} when event_id is unknown.
#    """
#    try:
#        if not payload.get("note") and payload.get("feedback"):
#            payload["note"] = payload["feedback"]

#        model = EventFeedbackIn(**payload)   # lets Pydantic do validation/aliasing
#        return post_event_feedback(model)    # call the real handler
#    except HTTPException:
#        raise
#    except Exception as e:
#        logger.exception("Error in /api/feedback: %s", e)
#        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/feedbacks")
def list_feedbacks(
    q: Optional[str] = Query(None, description="search note/body/entity_id"),
    entity_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    analysis_id: Optional[int] = Query(None),
    since: Optional[str] = Query(None, description="ISO timestamp"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """
    Return feedback notes with their linked event + entity info.
    """
    # Normalize 'since' to naive ISO (sqlite friendly)
    if since:
        try:
            s = since.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            since = dt.isoformat(timespec="seconds")
        except Exception:
            since = None

    sql = [
        "SELECT ef.id, ef.event_id, ef.ts, ef.note, ef.kind, ef.source,",
        "       ev.analysis_id, ev.category, ev.title, ev.body, ev.entity_ids",
        "FROM event_feedback ef",
        "JOIN analysis_events ev ON ev.id = ef.event_id",
        "WHERE 1=1"
    ]
    args: list = []

    if analysis_id:
        sql.append("AND ev.analysis_id = ?")
        args.append(int(analysis_id))
    if category:
        sql.append("AND ev.category = ?")
        args.append(category)
    if entity_id:
        sql.append("AND (ev.entity_ids LIKE ?)")
        args.append(f"%{entity_id}%")
    if q:
        # search note/body/title/entities
        sql.append("AND (ef.note LIKE ? OR ev.body LIKE ? OR ev.title LIKE ? OR ev.entity_ids LIKE ?)")
        like = f"%{q}%"
        args += [like, like, like, like]
    if since:
        sql.append("AND ef.ts >= ?")
        args.append(since)

    sql.append("ORDER BY ef.ts DESC LIMIT ? OFFSET ?")
    args += [int(limit), int(offset)]

    with db._conn() as c:
        rows = c.execute(" ".join(sql), args).fetchall()

    keys = ["id","event_id","ts","note","kind","source","analysis_id","category","title","body","entity_ids"]
    out = [dict(zip(keys, r)) for r in rows]

    # Add a parsed list for convenience
    for r in out:
        ids = (r.get("entity_ids") or "")
        r["entities"] = [e for e in ids.split(",") if e.strip()]
        r["ts"] = _ts_to_local_iso(r.get("ts"))
    return out


@app.get("/api/feedback/{fb_id}")
def get_feedback(fb_id: int = PathParam(...)):
    with db._conn() as c:
        r = c.execute(
            "SELECT ef.id, ef.event_id, ef.ts, ef.note, ef.kind, ef.source, "
            "       ev.analysis_id, ev.category, ev.title, ev.body, ev.entity_ids "
            "FROM event_feedback ef "
            "JOIN analysis_events ev ON ev.id = ef.event_id "
            "WHERE ef.id = ?",
            (int(fb_id),)
        ).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Feedback not found")
    keys = ["id","event_id","ts","note","kind","source","analysis_id","category","title","body","entity_ids"]
    row = dict(zip(keys, r))
    row["entities"] = [e for e in (row.get("entity_ids") or "").split(",") if e.strip()]
    row["ts"] = _ts_to_local_iso(row.get("ts"))
    return row


@app.put("/api/feedback/{fb_id}")
def update_feedback(fb_id: int, payload: FeedbackUpdate):
    fields = []
    args: list = []
    if payload.note is not None:
        fields.append("note = ?")
        args.append(payload.note.strip())
    if payload.kind is not None:
        fields.append("kind = ?")
        args.append(payload.kind.strip().lower())

    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to update")

    args.append(int(fb_id))
    with db._conn() as c:
        c.execute(f"UPDATE event_feedback SET {', '.join(fields)} WHERE id = ?", args)
        c.commit()
    return {"ok": True, "id": fb_id}


@app.delete("/api/feedback/{fb_id}")
def delete_feedback(fb_id: int):
    with db._conn() as c:
        c.execute("DELETE FROM event_feedback WHERE id = ?", (int(fb_id),))
        c.commit()
    return {"ok": True}


@app.get("/api/feedback")
def get_feedback(
    analysis_id: Optional[int] = Query(None),
    event_id: Optional[int] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """
    Return feedback notes.
    - If event_id is given: notes for that single event (joined with analysis_events to include body).
    - Else if analysis_id is given: all notes for that analysis (joined; includes body).
    """
    if not analysis_id and not event_id:
        raise HTTPException(status_code=400, detail="Provide analysis_id or event_id")

    with db._conn() as c:
        if event_id:
            rows = c.execute(
                """
                SELECT ef.id, ef.event_id, ef.ts, ef.note, ef.kind, ef.source,
                       ev.analysis_id, ev.category, ev.body
                FROM event_feedback ef
                JOIN analysis_events ev ON ev.id = ef.event_id
                WHERE ev.id = ?
                ORDER BY ef.ts DESC
                LIMIT ?
                """,
                (int(event_id), int(limit)),
            ).fetchall()
        else:  # analysis_id
            rows = c.execute(
                """
                SELECT ef.id, ef.event_id, ef.ts, ef.note, ef.kind, ef.source,
                       ev.analysis_id, ev.category, ev.body
                FROM event_feedback ef
                JOIN analysis_events ev ON ev.id = ef.event_id
                WHERE ev.analysis_id = ?
                ORDER BY ef.ts DESC
                LIMIT ?
                """,
                (int(analysis_id), int(limit)),
            ).fetchall()

    keys = ["id","event_id","ts","note","kind","source","analysis_id","category","body"]
    return [dict(zip(keys, r)) for r in rows]

@app.post("/api/feedback")
def post_feedback_alias(payload: dict = Body(...)):
    # Support legacy {feedback: "..."} too
    if not payload.get("note") and payload.get("feedback"):
        payload = {**payload, "note": payload["feedback"]}
    try:
        return _save_feedback_generic(payload)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in /api/feedback: %s", e)
        raise HTTPException(status_code=500, detail=str(e))        

@app.get("/api/followups")
def get_followups(analysis_id: int):
    with db._conn() as c:
        rows = c.execute(
            "SELECT id, label, code, status FROM followup_requests WHERE analysis_id=?",
            (analysis_id,)
        ).fetchall()
        return [dict(id=r[0], label=r[1], code=r[2], status=r[3]) for r in rows]

@app.post("/api/followup/run")
def run_followup(payload: FollowupRunRequest):
    aid = int(payload.analysis_id)
    code = payload.code
    if not aid or not code:
        raise HTTPException(status_code=400, detail="Missing analysis_id or code")

    try:
        # plug in your real handlers:
        if code == "list_automations":
            payload_out = do_list_automations(aid)     # implement elsewhere
        elif code == "show_energy_timeline":
            payload_out = do_energy_timeline(aid)      # implement elsewhere
        elif code == "troubleshoot_sensor":
            payload_out = do_troubleshoot(aid)         # implement elsewhere
        else:
            raise HTTPException(status_code=400, detail="Unknown code")

        with db._conn() as c:
            c.execute(
                "UPDATE followup_requests SET status='done' WHERE analysis_id=? AND code=?",
                (aid, code)
            )
            c.commit()
        return {"ok": True, "payload": payload_out}

    except HTTPException:
        # already meaningful
        raise
    except Exception as e:
        with db._conn() as c:
            c.execute(
                "UPDATE followup_requests SET status='failed' WHERE analysis_id=? AND code=?",
                (aid, code)
            )
            c.commit()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/run_history")
async def run_history(hours: int = Query(..., ge=1, le=48)):
    """
    Analyze the last N hours of history for (almost) all entities.
    Pulls topology + current states, fetches history in chunks, compresses it,
    injects USER CONTEXT MEMOS (feedback) into the prompt, then runs the model.
    """
    cfg = _load_config()
    mode = "passive"
    focus = f"Manual history analysis ({hours}h)"
    logger.info("Run history analysis: %sh", hours)

    try:
        if HAVE_REAL:
            ha = HAClient()
            gpt = OpenAIClient(model=cfg.get("model"))
            try:
                # 1) Topology
                topo = await fetch_topology_snapshot(ha, max_lines=TOPO_MAX_LINES)

                # 2) CURRENT STATE
                all_states = await ha.states()
                state_block = pack_states_for_prompt(all_states, max_lines=STATE_MAX_LINES)

                # 3) TIME WINDOW
                now = datetime.now(timezone.utc).replace(microsecond=0)
                start = (now - timedelta(hours=int(hours))).replace(microsecond=0)

                # 4) History for (almost) all entities, chunked
                chunk_size = int(cfg.get("history_chunk_size", 150))
                hist = await _fetch_history_all_entities(
                    ha,
                    all_states=all_states,
                    start_dt=start,
                    end_dt=now,
                    chunk_size=chunk_size,
                    minimal_response=True,
                )

                # 5) Compress history for the prompt
                try:
                    history_block = compress_history_for_prompt(
                        hist,
                        now=datetime.now(timezone.utc),
                        max_lines=int(_load_config().get("history_max_lines", HISTORY_MAX_LINES)),
                        jitter_sec=int(_load_config().get("history_jitter_sec", 90)),
                    )
                except Exception as e:
                    logger.warning("History pack failed: %s", e)
                    history_block = "(history unavailable)"

                # 6) Build USER CONTEXT MEMOS (entity-targeted + generic per category)
                # Use all visible entity_ids (clamped) so memos relevant to these devices/categories are included.
                max_all_entities = int(cfg.get("history_all_max_entities", 600))
                entity_ids_for_context = [
                    s.get("entity_id") for s in all_states if s.get("entity_id")
                ][:max_all_entities]

                try:
                    # Preferred helper if you added it (formats + clamps internally)
                    context_block = _build_context_memos_block(entity_ids_for_context)  # type: ignore[name-defined]
                except NameError:
                    # Fallback inline builder using _load_context_memos + clamp_chars
                    try:
                        cats = ["security", "comfort", "energy", "anomalies", "presence"]
                        parts = []
                        for cat in cats:
                            memos = _load_context_memos(entity_ids_for_context, cat)
                            # de-dupe + clamp count per category
                            memos = list(dict.fromkeys([m.strip() for m in memos if m and m.strip()]))[:6]
                            if memos:
                                parts.append("### " + cat.title() + "\n" + "\n".join(f"- {m}" for m in memos))
                        block = "\n\n".join(parts)
                        context_block = clamp_chars(block, 2000)
                    except Exception:
                        context_block = ""

                logger.info("Context memos included (history run): %d chars", len(context_block or ""))

                # 7) Compose prompt (no recent EVENT_BUFFER here by design)
                user = compose_user_prompt(
                    lang=cfg.get("language", "en"),
                    hours=hours,
                    topo=topo,
                    state_block=state_block,
                    history_block=history_block,
                    events_block="(none)",        # history run intentionally omits live event bullets
                    context_block=context_block,  # NEW
                )

                # 8) Call the text model
                summary = gpt.complete_text(SYSTEM_PASSIVE, user)
                actions: list = []

            finally:
                await ha.close()
        else:
            summary = f"History analysis for {hours} hours (simulated)."
            actions = []

        # 9) Persist (same as /api/run)
        row = db.add_analysis(mode, focus, summary, json.dumps(actions))

        if isinstance(row, (list, tuple)):
            row_id, row_ts = row[0], row[1]

            # Events
            events = _extract_events_from_summary(row_id, row_ts, summary)
            if events:
                with db._conn() as c:
                    c.executemany(
                        "INSERT OR IGNORE INTO analysis_events (analysis_id, ts, category, title, body, entity_ids) VALUES (?,?,?,?,?,?)",
                        events
                    )
                    c.commit()

            # Followups
            fups = _extract_followups(row_id, row_ts, summary)
            if fups:
                with db._conn() as c:
                    c.executemany(
                        "INSERT INTO followup_requests (analysis_id, ts, label, code) VALUES (?,?,?,?)",
                        fups
                    )
                    c.commit()

        # 10) Notify
        if HAVE_REAL:
            try:
                ha_notify = HAClient()
                await ha_notify.notify(f"HomeGPT – History Analysis ({hours}h)", summary)
            except Exception as notify_exc:
                logger.warning("Failed to send notification: %s", notify_exc)
            finally:
                try:
                    await ha_notify.close()
                except Exception:
                    pass

        # 11) Normalize response for UI
        return {
            "status": "ok",
            "summary": summary,
            "actions": actions,
            "row": (
                {"id": row[0], "ts": _ts_to_local_iso(row[1]), "mode": row[2], "focus": row[3], "summary": row[4], "actions": row[5]}
                if isinstance(row, (list, tuple))
                else row
            ),
            "diag": {
                "hours": hours,
                "chunk_size": int(cfg.get("history_chunk_size", 150)),
                "max_all_entities": int(cfg.get("history_all_max_entities", 600)),
                "context_block_len": len(context_block) if 'context_block' in locals() and context_block else 0,
            },
        }

    except Exception as exc:
        logger.exception("Error in run_history: %s", exc)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})



@app.post("/api/run")
async def run_analysis(request: AnalysisRequest = Body(...)):
    """
    Manual/explicit run from the UI.
    Passive: snapshots event buffer under a lock, clears it, fetches topology,
             pulls history, injects context memos, and calls the text model.
    Active : unchanged JSON action planner.
    """
    cfg = _load_config()
    mode = (request.mode or cfg.get("mode", "passive")).lower()
    focus = request.focus or ""
    logger.info("Run analysis (UI): mode=%s focus=%s", mode, focus)

    try:
        if HAVE_REAL:
            ha = HAClient()
            gpt = OpenAIClient(model=cfg.get("model"))
            try:
                if mode == "passive":
                    # 1) Snapshot & clear atomically (limit to last 2000 events for UI-sized runs)
                    async with EVENT_LOCK:
                        global EVENT_BYTES, EVENT_UNIQUE_IDS
                        events = EVENT_BUFFER[-2000:]
                        EVENT_BUFFER.clear()
                        EVENT_BYTES = 0
                        EVENT_UNIQUE_IDS.clear()

                    # 2) Topology
                    topo = await fetch_topology_snapshot(ha, max_lines=TOPO_MAX_LINES)

                    # 3) CURRENT STATE
                    all_states = await ha.states()
                    state_block = pack_states_for_prompt(all_states, max_lines=STATE_MAX_LINES)

                    # 4) HISTORY (prefer recent event entities, else subset of states)
                    cfg_hours = int(_load_config().get("history_hours", DEFAULT_HISTORY_HOURS))
                    now = datetime.now(timezone.utc).replace(microsecond=0)
                    start = (now - timedelta(hours=cfg_hours)).replace(microsecond=0)

                    entity_ids = sorted({e.get("entity_id") for e in events if e.get("entity_id")})[:100]
                    if not entity_ids:
                        entity_ids = sorted({s.get("entity_id") for s in all_states if s.get("entity_id")})[:300]

                    logger.info("History query (UI run): %d ids (first 15): %s", len(entity_ids), entity_ids[:15])

                    # Fetch history with a permissive fallback
                    try:
                        hist = await ha.history_period(
                            start.isoformat(timespec="seconds"),
                            now.isoformat(timespec="seconds"),
                            entity_ids=entity_ids,
                            minimal_response=True,
                            include_start_time_state=True,
                            significant_changes_only=None,
                        )
                    except Exception as e:
                        logger.warning("History fetch failed (first attempt): %s", e)
                        try:
                            hist = await ha.history_period(
                                start.isoformat(timespec="seconds"),
                                now.isoformat(timespec="seconds"),
                                entity_ids=entity_ids,
                                minimal_response=False,
                                include_start_time_state=True,
                                significant_changes_only=False,  # most permissive
                            )
                        except Exception as e2:
                            logger.warning("History fetch failed (second attempt): %s", e2)
                            hist = []

                    # Diagnostics
                    try:
                        groups = len(hist) if isinstance(hist, list) else 0
                        rows = sum(len(g) for g in hist if isinstance(g, list))
                        logger.info("History fetched for prompt (UI run): groups=%d total_rows=%d", groups, rows)
                    except Exception:
                        pass

                    # 5) Compress history (always set history_block)
                    try:
                        history_block = compress_history_for_prompt(
                            hist,
                            now=datetime.now(timezone.utc),
                            max_lines=int(_load_config().get("history_max_lines", HISTORY_MAX_LINES)),
                            jitter_sec=int(_load_config().get("history_jitter_sec", 90)),
                        )
                    except Exception as e:
                        logger.warning("History pack failed: %s", e)
                        history_block = "(history unavailable)"

                    # 6) Recent event bullets (from the snapshot we took)
                    bullets = [
                        f"{e['ts']} · {e['entity_id']} : {e['from']} → {e['to']}"
                        for e in events
                    ]
                    events_block = "\n".join(bullets) if bullets else "(none)"

                    # 7) USER CONTEXT MEMOS (entity-targeted + generic per category)
                    # Use all event entity_ids (or fall back to history entity_ids) to scope the memos
                    entity_ids_for_context = sorted({e.get("entity_id") for e in events if e.get("entity_id")}) or entity_ids
                    try:
                        # If you created a helper that formats + clamps:
                        context_block = _build_context_memos_block(entity_ids_for_context)  # type: ignore[name-defined]
                    except NameError:
                        # Fallback: inline build using _load_context_memos
                        try:
                            cats = ["security", "comfort", "energy", "anomalies", "presence"]
                            parts = []
                            for cat in cats:
                                memos = _load_context_memos(entity_ids_for_context, cat)
                                memos = list(dict.fromkeys([m.strip() for m in memos if m and m.strip()]))[:6]
                                if memos:
                                    parts.append("### " + cat.title() + "\n" + "\n".join(f"- {m}" for m in memos))
                            block = "\n\n".join(parts)
                            context_block = clamp_chars(block, 2000)  # keep tight
                        except Exception:
                            context_block = ""
                    logger.info("Context memos included (UI run): %d chars", len(context_block or ""))

                    # 8) Compose prompt (now includes context_block)
                    user = compose_user_prompt(
                        lang=cfg.get("language", "en"),
                        hours=cfg_hours,
                        topo=topo,
                        state_block=state_block,
                        history_block=history_block,
                        events_block=events_block,
                        context_block=context_block,   # <— new
                    )

                    # 9) Call the text model
                    summary = gpt.complete_text(SYSTEM_PASSIVE, user)
                    actions = []

                else:
                    # ACTIVE mode (JSON plan)
                    states = await ha.states()
                    lines = [f"{s['entity_id']}={s['state']}" for s in states[:400]]
                    user = f"Mode: {mode}\nCurrent states (subset):\n" + "\n".join(lines)
                    plan = gpt.complete_json(SYSTEM_ACTIVE, user, schema=ACTIONS_JSON_SCHEMA)
                    summary = plan.get("text") or plan.get("summary") or "No summary."
                    actions = plan.get("actions") or []

            finally:
                await ha.close()

        else:
            # Fallback (mocked)
            summary = f"Analysis in {mode} mode. Focus: {focus or 'General'}."
            actions = ["light.turn_off living_room", "climate.set_temperature bedroom 20°C"]

        # Persist the analysis
        row = db.add_analysis(mode, focus, summary, json.dumps(actions))

        # Extract events + followups (same as run_history)
        if isinstance(row, (list, tuple)):
            row_id, row_ts = row[0], row[1]

            # Events
            events = _extract_events_from_summary(row_id, row_ts, summary)
            if events:
                with db._conn() as c:
                    c.executemany(
                        "INSERT OR IGNORE INTO analysis_events (analysis_id, ts, category, title, body, entity_ids) VALUES (?,?,?,?,?,?)",
                        events
                    )
                    c.commit()

            # Followups
            fups = _extract_followups(row_id, row_ts, summary)
            if fups:
                with db._conn() as c:
                    c.executemany(
                        "INSERT INTO followup_requests (analysis_id, ts, label, code) VALUES (?,?,?,?)",
                        fups,
                    )
                    c.commit()

        # Notify HA so the result is visible immediately
        if HAVE_REAL:
            try:
                ha_notify = HAClient()
                await ha_notify.notify("HomeGPT – Analysis", summary)
            except Exception as notify_exc:
                logger.warning("Failed to send notification: %s", notify_exc)
            finally:
                try:
                    await ha_notify.close()
                except Exception:
                    pass

        # Normalize row shape for the UI
        return {
            "status": "ok",
            "summary": summary,
            "actions": actions,
            "row": (
                {"id": row[0], "ts": _ts_to_local_iso(row[1]), "mode": row[2], "focus": row[3], "summary": row[4], "actions": row[5]}
                if isinstance(row, (list, tuple))
                else row
            ),
        }

    except Exception as exc:
        logger.exception("Error in run_analysis: %s", exc)
        return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})



@app.get("/api/history")
def history():
    """
    Return the most recent analyses as dictionaries for the UI.

    Fields:
      - id: row id
      - ts: ISO timestamp
      - mode: passive/active
      - focus: optional focus string
      - summary: model summary text
      - actions: JSON string or list (driver dependent)
    """
    rows = db.get_analyses(50)
    result: list[dict] = []
    for row in rows:
        try:
            rid, ts, mode, focus, summary, actions_json = row[:6]
            result.append(
                {
                    "id": rid,
                    "ts": _ts_to_local_iso(ts),   # was ts
                    "mode": mode,
                    "focus": focus,
                    "summary": summary,
                    "actions": actions_json,
                }
            )
        except Exception:
            if isinstance(row, dict):
                result.append(row)
            else:
                logger.warning(f"Unexpected row format in history: {row}")
    return result


@app.get("/api/history/{analysis_id}")
def get_history_item(analysis_id: int):
    row = db.get_analysis(analysis_id)
    if isinstance(row, dict):
        return row
    try:
        rid, ts, mode, focus, summary, actions_json = row[:6]
        return {
                    "id": rid,
                    "ts": _ts_to_local_iso(ts),   # was ts
                    "mode": mode,
                    "focus": focus,
                    "summary": summary,
                    "actions": actions_json,
        }
    except Exception:
        logger.warning(f"Unexpected row format in history item: {row}")
        return row

"""
@app.post("/api/event_feedback")
def post_event_feedback(payload: EventFeedbackIn):
    note = (payload.note or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="Missing note")

    eid = payload.event_id
    if not eid:
        aid = payload.analysis_id
        body = (payload.body or "").strip()
        if aid and body:
            with db._conn() as c:
                row = c.execute(
                    "SELECT id FROM analysis_events WHERE analysis_id=? AND body=? LIMIT 1",
                    (aid, body)
                ).fetchone()
                if row:
                    eid = int(row[0])
                else:
                    from datetime import datetime
                    ent_ids = ",".join(_extract_entity_ids(body))
                    ts = datetime.utcnow().isoformat()
                    c.execute(
                        "INSERT INTO analysis_events (analysis_id, ts, category, title, body, entity_ids) "
                        "VALUES (?,?,?,?,?,?)",
                        (aid, ts, (payload.category or "generic"), body[:140], body, ent_ids)
                    )
                    eid = c.lastrowid
                    c.commit()
        if not eid:
            raise HTTPException(status_code=400, detail="Missing event_id or resolvable analysis/body")

    ts = datetime.utcnow().isoformat()
    with db._conn() as c:
        # old schema compatibility: try with source, fall back if column is missing
        try:
            c.execute(
                "INSERT INTO event_feedback (event_id, ts, note, kind, source) VALUES (?,?,?,?,?)",
                (int(eid), ts, note, (payload.kind or "context"), "user")
            )
        except Exception as ex:
            if "no column named source" in str(ex).lower():
                c.execute(
                    "INSERT INTO event_feedback (event_id, ts, note, kind) VALUES (?,?,?,?)",
                    (int(eid), ts, note, (payload.kind or "context"))
                )
            else:
                raise
        c.commit()

    return {"ok": True}
"""

@app.post("/api/event_feedback")
def post_event_feedback_route(payload: dict = Body(...)):
    try:
        return _save_feedback_generic(payload)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in /api/event_feedback: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/events")
def get_events(
    since: Optional[str] = Query(None, description="ISO timestamp; 'Z' allowed"),
    category: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    # Normalize 'since'
    if since:
        try:
            s = since.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            since = dt.isoformat(timespec="seconds")
        except Exception:
            since = None

    q = (
        "SELECT ev.id, ev.analysis_id, ev.ts, ev.category, ev.title, ev.body, ev.entity_ids, "
        "(SELECT COUNT(*) FROM event_feedback ef WHERE ef.event_id = ev.id) AS feedback_count "
        "FROM analysis_events ev WHERE 1=1"
    )
    args: list = []
    if category:
        q += " AND ev.category=?"
        args.append(category)
    if since:
        q += " AND ev.ts>=?"
        args.append(since)
    q += " ORDER BY ev.ts DESC LIMIT ?"
    args.append(int(limit))

    try:
        with db._conn() as c:
            rows = c.execute(q, args).fetchall()
        keys = ["id","analysis_id","ts","category","title","body","entity_ids","feedback_count"]
        return [dict(zip(keys, r)) for r in rows]
    except Exception as e:
        logger.exception("Error in /api/events: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch events")



@app.get("/api/settings")
def get_settings():
    return _load_config()


@app.post("/api/settings")
def update_settings(settings: Settings):
    logger.info(f"Updating settings from UI: {settings}")
    cfg = _load_config()
    data = {k: v for k, v in settings.dict().items() if v is not None}
    cfg.update(data)
    _save_config(cfg)
    return {"status": "ok"}


# ---------------- Config Helpers ----------------
def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return {"mode": "passive", "model": "gpt-5", "dry_run": True}


def _save_config(data: dict) -> None:
    CONFIG_PATH.write_text(yaml.safe_dump(data))


# ---------------- Background Event Listener ----------------
async def ha_event_listener():
    if not HAVE_REAL:
        logger.warning("HA integration not available — event listener disabled.")
        return

    ha = HAClient()
    while True:
        try:
            async for evt in ha.websocket_events():
                try:
                    data = {
                        "ts": evt["time_fired"],
                        "entity_id": evt["data"]["entity_id"],
                        "from": evt["data"]["old_state"]["state"] if evt["data"]["old_state"] else None,
                        "to": evt["data"]["new_state"]["state"] if evt["data"]["new_state"] else None,
                    }
                    async with EVENT_LOCK:
                        global EVENT_BYTES, EVENT_UNIQUE_IDS
                        EVENT_BUFFER.append(data)
                        if len(EVENT_BUFFER) > EVENT_BUFFER_MAX:
                            del EVENT_BUFFER[: len(EVENT_BUFFER) - EVENT_BUFFER_MAX]

                        # --- size pressure accounting (very cheap) ---
                        eid = data.get("entity_id") or ""
                        oldv = str(data.get("from") or "")
                        newv = str(data.get("to") or "")
                        # approximate length of the bullet line we will render later
                        approx_len = 32 + len(eid) + len(oldv) + len(newv)  # timestamp+separators ~= 32
                        EVENT_BYTES += approx_len
                        if eid:
                            EVENT_UNIQUE_IDS.add(eid)

                        # --- classic event-count trigger ---
                        count_pressure = len(EVENT_BUFFER) >= AUTO_ANALYSIS_EVENT_THRESHOLD

                        # --- size pressure trigger (fires earlier than count) ---
                        size_pressure = (
                            EVENT_BYTES >= EVENTS_TRIGGER_CHARS
                            or len(EVENT_UNIQUE_IDS) >= EVENTS_TRIGGER_UNIQUE
                        )

                        # debounce + “not already analyzing”
                        now_mono = asyncio.get_event_loop().time()
                        global _last_auto_run_ts
                        # choose the right cool-down: size pressure can be a bit more frequent
                        last_ts = _last_auto_run_ts or 0.0
                        recent_enough_count = (now_mono - last_ts) >= AUTO_ANALYSIS_MIN_INTERVAL_SEC
                        recent_enough_size  = (now_mono - last_ts) >= AUTO_SIZE_MIN_INTERVAL_SEC
                        idle = not _analysis_in_progress.is_set()

                    # Decide reason & fire (don’t block the event loop)
                    if idle and ((size_pressure and recent_enough_size) or (count_pressure and recent_enough_count)):
                        reason = "Auto (size pressure)" if size_pressure else "Auto (event threshold)"
                        asyncio.create_task(_auto_trigger(reason))

                except Exception as ex:
                    logger.exception(f"Error processing event: {ex}")
        except Exception as ex:
            logger.error(f"HA websocket disconnected: {ex}, reconnecting in 5s...")
            await asyncio.sleep(5)


async def _auto_trigger(reason: str = "auto"):
    _analysis_in_progress.set()
    try:
        cfg = _load_config()
        mode = cfg.get("mode", "passive").lower()
        result = await _perform_analysis(mode, focus=reason, trigger="auto")
        global _last_auto_run_ts
        _last_auto_run_ts = asyncio.get_event_loop().time()
        logger.info("Auto analysis stored row=%s (%s)", result.get("row"), reason)
    except Exception as e:
        logger.exception("Auto analysis failed: %s", e)
    finally:
        _analysis_in_progress.clear()



@app.on_event("startup")
async def startup_event():
    asyncio.create_task(ha_event_listener())
    logger.info("HomeGPT API started — background event listener running.")
