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
import os
from zoneinfo import ZoneInfo
import asyncio
import json
import logging
from pathlib import Path
import math
from typing import Iterable
from datetime import datetime, timezone
from statistics import mean
from typing import Any
import re

import yaml
from fastapi import FastAPI, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timezone, timedelta

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
from datetime import datetime, timezone, timedelta
# Import DB, models, analyzer
try:
    from homegpt.api.models import AnalysisRequest, Settings
    from homegpt.api import db, analyzer
except ImportError:
    from pydantic import BaseModel  # type: ignore

    class AnalysisRequest(BaseModel):  # type: ignore
        mode: str | None = None
        focus: str | None = None

    class Settings(BaseModel):  # type: ignore
        openai_api_key: str | None = None
        model: str | None = None
        mode: str | None = None
        summarize_time: str | None = None
        control_allowlist: list[str] | None = None
        max_actions_per_hour: int | None = None
        dry_run: bool | None = None
        log_level: str | None = None
        language: str | None = None

    class db:  # type: ignore
        @staticmethod
        def init_db() -> None: ...
        @staticmethod
        def get_analyses(limit: int): return []
        @staticmethod
        def get_analysis(aid: int): return {}
        @staticmethod
        def add_analysis(mode, focus, summary, actions): return {}

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
    opts = re.findall(r'(?:\\(|\\b)(\\d+)[\\)\\.\\:]\\s*(.+?)(?=\\n\\d+|\\Z)', summary, flags=re.S)
    rows = []
    for num, label in opts:
        label = " ".join(label.split())
        if "list" in label.lower() and "automation" in label.lower():
            code = "list_automations"
        elif "timeline" in label.lower() and ("energy" in label.lower() or "sensor" in label.lower()):
            code = "show_energy_timeline"
        elif "troubleshoot" in label.lower() or "faulty" in label.lower():
            code = "troubleshoot_sensor"
        else:
            continue
        rows.append((aid, ts, label[:255], code))
    return rows

    fups = _extract_followups(row_id, row_ts, summary)
    if fups:
        with db._conn() as c:
            c.executemany("INSERT INTO followup_requests (analysis_id, ts, label, code) VALUES (?,?,?,?)", fups)
            c.commit()

def compose_user_prompt(*, lang: str, hours: int | None, topo: str, state_block: str, history_block: str, events_block: str = "") -> str:
    topo          = clamp_chars(strip_noise(topo), TOPO_MAX_CHARS)
    state_block   = clamp_chars(strip_noise(state_block), STATE_MAX_CHARS)
    history_block = clamp_chars(history_block, HISTORY_MAX_CHARS)
    events_block  = clamp_chars(events_block, EVENTS_MAX_CHARS) if events_block else ""

    header = (
        f"Language: {lang}.\n"
        "First, topology; then CURRENT STATE; then compressed history"
        + ("; then recent events" if events_block else "")
        + ". Use CURRENT STATE as ground truth (avoid guessing).\n\n"
    )

    body = f"{topo}\n\n{state_block}\n\n{history_block}\n\n"
    if events_block:
        body += "EVENTS:\n" + events_block

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
                # Snapshot & clear events atomically
                async with EVENT_LOCK:
                    global EVENT_BYTES, EVENT_UNIQUE_IDS
                    events = EVENT_BUFFER[-EVENT_BUFFER_MAX:]
                    EVENT_BUFFER.clear()
                    EVENT_BYTES = 0
                    EVENT_UNIQUE_IDS.clear()

                if not events:
                    # no events → skip auto‑analysis
                    return {"summary": "No notable events recorded.", "actions": [], "row": None}
                else:
                    # ----- Topology -----
                    topo = await fetch_topology_snapshot(ha, max_lines=TOPO_MAX_LINES)

                    # ----- Current state (ground truth) -----
                    all_states = await ha.states()
                    state_block = pack_states_for_prompt(all_states, max_lines=STATE_MAX_LINES)

                    # ----- History for ALL entities (last N hours) -----
                    cfg_hours = int(_load_config().get("history_hours", DEFAULT_HISTORY_HOURS))
                    now = datetime.now(timezone.utc).replace(microsecond=0)
                    start = (now - timedelta(hours=cfg_hours)).replace(microsecond=0)
                
                    # after you have: events, all_states, start, now

                    # ---- Build entity list for history ----
                    entity_ids = [e['entity_id'] for e in events[-10:]]  # last few entity IDs from EVENT_BUFFER
                    notes = _load_context_memos(entity_ids, category="security")  # call per category if you prefer
                    if notes:
                        user += "\n\nKnown context / user notes:\n" + "\n".join(f"- {n}" for n in notes)

                    if not entity_ids:
                        # fallback: take a subset of current states
                        entity_ids = sorted({s.get("entity_id") for s in all_states if s.get("entity_id")})[:300]

                    logger.info("History query (worker): %d ids (first 15): %s", len(entity_ids), entity_ids[:15])

                    # ---- Fetch history with a permissive fallback ----
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
                                significant_changes_only=False,   # most permissive
                            )
                        except Exception as e2:
                            logger.warning("History fetch failed (second attempt): %s", e2)
                            hist = []

                    # ---- Diagnostics: what did we actually get? ----
                    try:
                        groups = len(hist) if isinstance(hist, list) else 0
                        rows = sum(len(g) for g in hist if isinstance(g, list))
                        logger.info("History fetched for prompt (worker): groups=%d total_rows=%d", groups, rows)
                    except Exception:
                        pass

                    # ✅ always set history_block
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
                    
                    # ----- Recent event bullets (use the snapshot we just took) -----
                    bullets = [
                        f"{e['ts']} · {e['entity_id']} : {e['from']} → {e['to']}"
                        for e in events[-AUTO_ANALYSIS_EVENT_THRESHOLD:]
                    ]
                    events_block = "\n".join(bullets) if bullets else "(none)"

                    # ----- Compose user message -----
                    user = compose_user_prompt(
                        lang=cfg.get("language", "en"),
                        hours=cfg_hours,
                        topo=topo,
                        state_block=state_block,
                        history_block=history_block,
                        events_block=events_block,
                    )

                    summary = gpt.complete_text(SYSTEM_PASSIVE, user)
                    actions = []

            else:
                states = await ha.states()
                lines = [f"{s['entity_id']}={s['state']}" for s in states[:400]]
                user = f"Mode: {mode}\nCurrent states (subset):\n" + "\n".join(lines)
                plan = gpt.complete_json(SYSTEM_ACTIVE, user, schema=ACTIONS_JSON_SCHEMA)
                summary = plan.get("text") or plan.get("summary") or "No summary."
                actions = plan.get("actions") or []

        finally:
            await ha.close()

    else:
        summary = f"Analysis in {mode} mode. Focus: {focus or 'General'}."
        actions = ["light.turn_off living_room", "climate.set_temperature bedroom 20°C"]

    # Persist
    row = db.add_analysis(mode, focus or (f"{trigger} trigger"), summary, json.dumps(actions))

    if isinstance(row, (list, tuple)):
        row_id, row_ts = row[0], row[1]
        events = _extract_events_from_summary(row_id, row_ts, summary)
        if events:
            with db._conn() as c:
                c.executemany(
                    "INSERT OR IGNORE INTO analysis_events (analysis_id, ts, category, title, body, entity_ids) VALUES (?, ?, ?, ?, ?, ?)",
                    events
                )
                c.commit()

    # Notify (nice for auto triggers too)
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

@app.get("/api/followups")
def get_followups(analysis_id: int):
    with db._conn() as c:
        rows = c.execute("SELECT id, label, code, status FROM followup_requests WHERE analysis_id=?", (analysis_id,)).fetchall()
        return [dict(id=r[0], label=r[1], code=r[2], status=r[3]) for r in rows]

@app.post("/api/followup/run")
def run_followup():
    data = request.get_json(force=True) or {}
    aid  = int(data.get("analysis_id"))
    code = data.get("code")
    if not aid or not code: abort(400, "Missing analysis_id or code")
    # implement handlers:
    try:
        if code == "list_automations":
            payload = do_list_automations(aid)  # your implementation
        elif code == "show_energy_timeline":
            payload = do_energy_timeline(aid)
        elif code == "troubleshoot_sensor":
            payload = do_troubleshoot(aid)
        else:
            abort(400, "Unknown code")
        with db._conn() as c:
            c.execute("UPDATE followup_requests SET status='done' WHERE analysis_id=? AND code=?", (aid, code))
        return {"ok": True, "payload": payload}
    except Exception:
        with db._conn() as c:
            c.execute("UPDATE followup_requests SET status='failed' WHERE analysis_id=? AND code=?", (aid, code))
        raise

@app.post("/api/run_history")
async def run_history(hours: int = Query(..., ge=1, le=48)):
    """
    Analyze the last N hours of history for (almost) all entities.
    History is pulled in chunks and compressed before sending to the model.
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
                # Topology
                topo = await fetch_topology_snapshot(ha, max_lines=TOPO_MAX_LINES)

                # CURRENT STATE
                all_states = await ha.states()
                state_block = pack_states_for_prompt(all_states, max_lines=STATE_MAX_LINES)

                # TIME WINDOW
                now = datetime.now(timezone.utc).replace(microsecond=0)
                start = (now - timedelta(hours=int(hours))).replace(microsecond=0)

                # Chunk size & caps (configurable)
                chunk_size = int(cfg.get("history_chunk_size", 150))

                # ALL-entities history (chunked)
                hist = await _fetch_history_all_entities(
                    ha,
                    all_states=all_states,
                    start_dt=start,
                    end_dt=now,
                    chunk_size=chunk_size,
                    minimal_response=True,
                )

                # Pack
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

                # Compose prompt (no recent EVENT_BUFFER here by design)
                user = compose_user_prompt(
                    lang=cfg.get("language", "en"),
                    hours=hours,
                    topo=topo,
                    state_block=state_block,
                    history_block=history_block,
                )


                summary = gpt.complete_text(SYSTEM_PASSIVE, user)
                actions: list = []

            finally:
                await ha.close()
        else:
            summary = f"History analysis for {hours} hours (simulated)."
            actions = []

        # Persist just like /api/run
        row = db.add_analysis(mode, focus, summary, json.dumps(actions))

        if isinstance(row, (list, tuple)):
            row_id, row_ts = row[0], row[1]
            events = _extract_events_from_summary(row_id, row_ts, summary)
            if events:
                with db._conn() as c:
                    c.executemany(
                        "INSERT OR IGNORE INTO analysis_events (analysis_id, ts, category, title, body, entity_ids) VALUES (?, ?, ?, ?, ?, ?)",
                        events
                    )
                    c.commit()

        # Notify
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
             and calls the text model.
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

                    # 4) HISTORY (ALL entities for last N hours)
                    cfg_hours = int(_load_config().get("history_hours", DEFAULT_HISTORY_HOURS))
                    now = datetime.now(timezone.utc).replace(microsecond=0)
                    start = (now - timedelta(hours=cfg_hours)).replace(microsecond=0)
                
                    # after you have: events, all_states, start, now

                    # ---- Build entity list for history ----
                    entity_ids = sorted({e.get("entity_id") for e in events if e.get("entity_id")})[:100]
                    if not entity_ids:
                        # fallback: take a subset of current states
                        entity_ids = sorted({s.get("entity_id") for s in all_states if s.get("entity_id")})[:300]

                    logger.info("History query (worker): %d ids (first 15): %s", len(entity_ids), entity_ids[:15])

                    # ---- Fetch history with a permissive fallback ----
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
                                significant_changes_only=False,   # most permissive
                            )
                        except Exception as e2:
                            logger.warning("History fetch failed (second attempt): %s", e2)
                            hist = []

                    # ---- Diagnostics: what did we actually get? ----
                    try:
                        groups = len(hist) if isinstance(hist, list) else 0
                        rows = sum(len(g) for g in hist if isinstance(g, list))
                        logger.info("History fetched for prompt (worker): groups=%d total_rows=%d", groups, rows)
                    except Exception:
                        pass

                    # ✅ always set history_block
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

                    # 5) Recent event bullets from the snapshot we took
                    bullets = [
                        f"{e['ts']} · {e['entity_id']} : {e['from']} → {e['to']}"
                        for e in events
                    ]
                    events_block = "\n".join(bullets) if bullets else "(none)"

                    # 6) Compose prompt
                    user = compose_user_prompt(
                        lang=cfg.get("language", "en"),
                        hours=cfg_hours,
                        topo=topo,
                        state_block=state_block,
                        history_block=history_block,
                        events_block=events_block,
                    )

                    # 7) Call the text model
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
            summary = f"Analysis in {mode} mode. Focus: {focus or 'General'}."
            actions = ["light.turn_off living_room", "climate.set_temperature bedroom 20°C"]

        # Persist the analysis
        row = db.add_analysis(mode, focus, summary, json.dumps(actions))

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

# POST /api/event_feedback
# body: { event_id, note, kind? }
@app.post("/api/event_feedback")
def post_event_feedback():
    data = request.get_json(force=True) or {}
    eid  = int(data.get("event_id"))
    note = (data.get("note") or "").strip()
    kind = (data.get("kind") or "context").strip()
    if not eid or not note: abort(400, "Missing event_id or note")

    ts = datetime.utcnow().isoformat()
    with db:
        db.execute("INSERT INTO event_feedback (event_id, ts, note, kind, source) VALUES (?,?,?,?,?)",
                   (eid, ts, note, kind, "user"))
    return jsonify({"ok": True})

# GET /api/events?since=ISO&category=security|comfort|...
@app.get("/api/events")
def get_events():
    q = "SELECT id, analysis_id, ts, category, title, body, entity_ids FROM analysis_events WHERE 1=1"
    args = []
    cat = request.args.get("category")
    if cat:
        q += " AND category=?"
        args.append(cat)
    since = request.args.get("since")
    if since:
        q += " AND ts>=?"
        args.append(since)
    q += " ORDER BY ts DESC LIMIT 200"
    rows = db.execute(q, args).fetchall()
    out = [dict(zip(["id","analysis_id","ts","category","title","body","entity_ids"], r)) for r in rows]
    return jsonify(out)


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
