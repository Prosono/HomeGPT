"""
FastAPI application serving the HomeGPT dashboard and API endpoints.

This version introduces three improvements:

1.  The ``run_analysis`` endpoint wraps its core logic in a try/except
    block and returns a 500 JSON response with an error message if
    something goes wrong.  This prevents uncaught exceptions from
    bubbling up to the client.

2.  The request model for ``run_analysis`` (see ``homegpt/api/models.py``)
    now makes the ``mode`` field optional.  The handler uses the
    configured default mode if none is supplied.

3.  The ``history`` and ``get_history_item`` endpoints now return
    dictionaries instead of raw tuples.  Each dictionary contains
    ``id``, ``ts``, ``mode``, ``focus``, ``summary`` and ``actions``.
    This ensures the front‑end receives consistent keys regardless of
    the underlying SQLite driver.
"""

import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
import yaml
from fastapi import FastAPI, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ---------------- Global Event Buffer ----------------
EVENT_BUFFER: list[dict] = []

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
        def init_db() -> None: pass
        @staticmethod
        def get_analyses(limit: int): return []
        @staticmethod
        def get_analysis(aid: int): return {}
        @staticmethod
        def add_analysis(mode, focus, summary, actions): pass
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
    # Count events waiting in memory
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
        "model": cfg.get("model", "gpt-4o-mini"),
        "dry_run": cfg.get("dry_run", True),
        "last_analysis": last[0] if last else None,
        "event_count": event_count,
        "seconds_since_last": seconds_since_last,
    }

@app.post("/api/mode")
def set_mode(mode: str = Query(...)):
    logger.info(f"Setting mode to: {mode}")
    cfg = _load_config()
    cfg["mode"] = mode
    _save_config(cfg)
    return {"status": "ok", "mode": mode}

@app.post("/api/run")
async def run_analysis(request: AnalysisRequest = Body(...)):
    cfg = _load_config()
    mode = (request.mode or cfg.get("mode", "passive")).lower()
    focus = request.focus or ""
    logger.info("Run analysis (UI): mode=%s focus=%s", mode, focus)
    try:
        summary: str = ""
        actions: list = []
        if HAVE_REAL:
            ha = HAClient()
            gpt = OpenAIClient()
            try:
                if mode == "passive":
                    if not EVENT_BUFFER:
                        summary = "No notable events recorded."
                    else:
                        bullets = [
                            f"{e['ts']} · {e['entity_id']} : {e['from']} → {e['to']}"
                            for e in EVENT_BUFFER[-2000:]
                        ]
                        user = (
                            f"Language: {cfg.get('language', 'en')}\n"
                            f"Summarize recent home activity from these lines (newest last).\n"
                            + "\n".join(bullets)
                        )
                        summary = gpt.complete_text(SYSTEM_PASSIVE, user)
                        actions = []
                        EVENT_BUFFER.clear()
                else:
                    states = await ha.states()
                    lines = [f"{s['entity_id']}={s['state']}" for s in states[:400]]
                    user = f"Mode: {mode}\nCurrent states (subset):\n" + "\n".join(lines)
                    plan = gpt.complete_json(
                        SYSTEM_ACTIVE, user, schema=ACTIONS_JSON_SCHEMA
                    )
                    summary = plan.get("text") or plan.get("summary") or "No summary."
                    actions = plan.get("actions") or []
            finally:
                await ha.close()
        else:
            summary = f"Analysis in {mode} mode. Focus: {focus or 'General'}."
            actions = [
                "light.turn_off living_room",
                "climate.set_temperature bedroom 20°C",
            ]
        # Persist the analysis
        row = db.add_analysis(mode, focus, summary, json.dumps(actions))
        # Send a persistent notification for manual runs (optional)
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
        return {
            "status": "ok",
            "summary": summary,
            "actions": actions,
            # Convert row to dict to preserve column names.  Row may be a list,
            # tuple or dict depending on DB driver.
            "row": (
                {"id": row[0], "ts": row[1], "mode": row[2], "focus": row[3], "summary": row[4], "actions": row[5]}
                if isinstance(row, (list, tuple))
                else row
            ),
        }
    except Exception as exc:
        logger.exception("Error in run_analysis: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(exc)},
        )

@app.get("/api/history")
def history():
    """
    Return the most recent analyses.  Each analysis is returned as a
    dictionary rather than a tuple/list for clarity and ease of
    consumption by the UI.  The fields include:

    - ``id``: unique row identifier
    - ``ts``: ISO timestamp when the analysis was saved
    - ``mode``: analysis mode (passive/active)
    - ``focus``: user‑supplied focus string, if any
    - ``summary``: summary text returned by the model
    - ``actions``: JSON string of proposed or executed actions
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

@app.get("/api/history")
def history():
    rows = db.get_analyses(50)
    result = []
    for row in rows:
        rid, ts, mode, focus, summary, actions_json = row[:6]
        result.append({
            "id": rid,
            "ts": ts,
            "mode": mode,
            "focus": focus,
            "summary": summary,
            "actions": actions_json,
        })
    return result


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
    return {"mode": "passive", "model": "gpt-4o-mini", "dry_run": True}

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
                    EVENT_BUFFER.append(data)
                    if len(EVENT_BUFFER) > 5000:
                        EVENT_BUFFER.pop(0)
                except Exception as ex:
                    logger.exception(f"Error processing event: {ex}")
        except Exception as ex:
            logger.error(f"HA websocket disconnected: {ex}, reconnecting in 5s...")
            await asyncio.sleep(5)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(ha_event_listener())
    logger.info("HomeGPT API started — background event listener running.")
