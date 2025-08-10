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

import asyncio
import json
import logging
from pathlib import Path

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


# ---------------- Global Event Buffer ----------------
EVENT_BUFFER: list[dict] = []
EVENT_BUFFER_MAX = 20000
AUTO_ANALYSIS_EVENT_THRESHOLD = 10000       # auto-run when we reach 2k events
AUTO_ANALYSIS_MIN_INTERVAL_SEC = 15 * 60   # debounce auto-runs (15 min)

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
                    events = EVENT_BUFFER[-EVENT_BUFFER_MAX:]
                    EVENT_BUFFER.clear()

                if not events:
                    summary = "No notable events recorded."
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
                
                    try:
                        hist = await ha.history_period(
                            start.isoformat(timespec="seconds"),
                            now.isoformat(timespec="seconds"),
                            entity_ids=None,
                            minimal_response=True,
                            include_start_time_state=True,
                            significant_changes_only=None,  # first attempt
                        )
                    except Exception as e:
                        logger.warning("History fetch failed (first attempt): %s", e)
                        try:
                            # second attempt: drop minimal_response (ha.py already does) AND try significant changes only
                            hist = await ha.history_period(
                                start.isoformat(timespec="seconds"),
                                now.isoformat(timespec="seconds"),
                                entity_ids=None,
                                minimal_response=False,
                                include_start_time_state=True,
                                significant_changes_only=True,
                            )
                        except Exception as e2:
                            logger.warning("History fetch failed (second attempt): %s", e2)
                            hist = []  # keep analysis going
                    
                    # ----- Recent event bullets (use the snapshot we just took) -----
                    bullets = [
                        f"{e['ts']} · {e['entity_id']} : {e['from']} → {e['to']}"
                        for e in events[-AUTO_ANALYSIS_EVENT_THRESHOLD:]
                    ]
                    events_block = "\n".join(bullets) if bullets else "(none)"

                    # ----- Compose user message -----
                    user = (
                        f"Language: {cfg.get('language', 'en')}.\n"
                        "First, a compact topology snapshot; then the CURRENT STATE for all entities; "
                        f"then a compressed history for the last {cfg_hours} hours; "
                        "then the raw recent events (newest last). "
                        "Treat CURRENT STATE as ground truth (avoid guessing).\n\n"
                        f"{topo}\n\n"
                        f"{state_block}\n\n"
                        f"{history_block}\n\n"
                        "EVENTS:\n" + events_block
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
                        events = EVENT_BUFFER[-2000:]
                        EVENT_BUFFER.clear()

                    # 2) Topology
                    topo = await fetch_topology_snapshot(ha, max_lines=TOPO_MAX_LINES)

                    # 3) CURRENT STATE
                    all_states = await ha.states()
                    state_block = pack_states_for_prompt(all_states, max_lines=STATE_MAX_LINES)

                    # 4) HISTORY (ALL entities for last N hours)
                    cfg_hours = int(_load_config().get("history_hours", DEFAULT_HISTORY_HOURS))
                    now = datetime.now(timezone.utc).replace(microsecond=0)
                    start = (now - timedelta(hours=cfg_hours)).replace(microsecond=0)
                
                    try:
                        hist = await ha.history_period(
                            start.isoformat(timespec="seconds"),
                            now.isoformat(timespec="seconds"),
                            entity_ids=None,
                            minimal_response=True,
                            include_start_time_state=True,
                            significant_changes_only=None,  # first attempt
                        )
                    except Exception as e:
                        logger.warning("History fetch failed (first attempt): %s", e)
                        try:
                            # second attempt: drop minimal_response (ha.py already does) AND try significant changes only
                            hist = await ha.history_period(
                                start.isoformat(timespec="seconds"),
                                now.isoformat(timespec="seconds"),
                                entity_ids=None,
                                minimal_response=False,
                                include_start_time_state=True,
                                significant_changes_only=True,
                            )
                        except Exception as e2:
                            logger.warning("History fetch failed (second attempt): %s", e2)
                            hist = []  # keep analysis going

                    # 5) Recent event bullets from the snapshot we took
                    bullets = [
                        f"{e['ts']} · {e['entity_id']} : {e['from']} → {e['to']}"
                        for e in events
                    ]
                    events_block = "\n".join(bullets) if bullets else "(none)"

                    # 6) Compose prompt
                    user = (
                        f"Language: {cfg.get('language', 'en')}.\n"
                        "First, topology; then CURRENT STATE; then compressed history; then recent events. "
                        "Use CURRENT STATE as ground truth (avoid guessing).\n\n"
                        f"{topo}\n\n"
                        f"{state_block}\n\n"
                        f"{history_block}\n\n"
                        "EVENTS:\n" + events_block
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
                {"id": row[0], "ts": row[1], "mode": row[2], "focus": row[3], "summary": row[4], "actions": row[5]}
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
                    "ts": ts,
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
            "ts": ts,
            "mode": mode,
            "focus": focus,
            "summary": summary,
            "actions": actions_json,
        }
    except Exception:
        logger.warning(f"Unexpected row format in history item: {row}")
        return row


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
                        EVENT_BUFFER.append(data)
                        if len(EVENT_BUFFER) > EVENT_BUFFER_MAX:
                            # bulk drop oldest
                            del EVENT_BUFFER[: len(EVENT_BUFFER) - EVENT_BUFFER_MAX]

                        # Check auto-trigger conditions
                        should_trigger = len(EVENT_BUFFER) >= AUTO_ANALYSIS_EVENT_THRESHOLD
                        now = asyncio.get_event_loop().time()
                        global _last_auto_run_ts
                        recent_enough = (_last_auto_run_ts is None) or (
                            (now - _last_auto_run_ts) >= AUTO_ANALYSIS_MIN_INTERVAL_SEC
                        )
                        idle = not _analysis_in_progress.is_set()

                    if should_trigger and recent_enough and idle:
                        # fire and forget (don’t block the event loop)
                        asyncio.create_task(_auto_trigger())

                except Exception as ex:
                    logger.exception(f"Error processing event: {ex}")
        except Exception as ex:
            logger.error(f"HA websocket disconnected: {ex}, reconnecting in 5s...")
            await asyncio.sleep(5)


async def _auto_trigger():
    # debounce + mark running
    _analysis_in_progress.set()
    try:
        cfg = _load_config()
        mode = cfg.get("mode", "passive").lower()
        result = await _perform_analysis(mode, focus="Auto (event threshold)", trigger="auto")
        # success → update timestamp
        global _last_auto_run_ts
        _last_auto_run_ts = asyncio.get_event_loop().time()
        logger.info("Auto analysis stored row=%s", result.get("row"))
    except Exception as e:
        logger.exception("Auto analysis failed: %s", e)
    finally:
        _analysis_in_progress.clear()


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(ha_event_listener())
    logger.info("HomeGPT API started — background event listener running.")
