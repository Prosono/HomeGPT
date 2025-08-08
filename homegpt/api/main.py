from fastapi import FastAPI, Request, Query
from fastapi import FastAPI, Depends, HTTPException, Body
from homegpt.app.main import EVENT_BUFFER  # import the buffer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import json
import yaml
import logging

# Import real models if available
try:
    from homegpt.api.models import AnalysisRequest, Settings
    from homegpt.api import db, analyzer
except ImportError:
    # Fallback for dev
    from pydantic import BaseModel
    class AnalysisRequest(BaseModel):
        mode: str
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
    import sqlite3
    class db:
        @staticmethod
        def init_db(): pass
        @staticmethod
        def get_analyses(limit): return []
        @staticmethod
        def get_analysis(aid): return {}
        @staticmethod
        def add_analysis(mode, focus, summary, actions): pass
    analyzer = None  # placeholder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("HomeGPT")

# at top: from homegpt.api import db
# try to import real clients:
try:
    from homegpt.app.ha import HAClient
    from homegpt.app.openai_client import OpenAIClient
    from homegpt.app.policy import SYSTEM_PASSIVE, SYSTEM_ACTIVE, ACTIONS_JSON_SCHEMA
    HAVE_REAL = True
except Exception:
    HAVE_REAL = False

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

# ---------------- API routes ----------------
@app.get("/api/status")
def get_status():
    cfg = _load_config()
    last = db.get_analyses(1)
    return {
        "mode": cfg.get("mode", "passive"),
        "model": cfg.get("model", "gpt-4o-mini"),
        "dry_run": cfg.get("dry_run", True),
        "last_analysis": last[0] if last else None,
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

    summary = ""
    actions = []

    if HAVE_REAL:
        ha = HAClient()
        gpt = OpenAIClient()
        try:
            if mode == "passive":
                # Use the same bullet creation logic as daily summary
                if not EVENT_BUFFER:
                    summary = "No notable events recorded."
                else:
                    bullets = [
                        f"{e['ts']} · {e['entity_id']} : {e['from']} → {e['to']}"
                        for e in EVENT_BUFFER[-2000:]
                    ]
                    user = (
                        f"Language: {cfg.get('language', 'en')}.\n"
                        f"Summarize recent home activity from these lines (newest last).\n"
                        + "\n".join(bullets)
                    )
                    res = gpt.complete_json(SYSTEM_PASSIVE, user)
                    summary = res.get("text") or json.dumps(res, indent=2)
                    actions = []
                    EVENT_BUFFER.clear()
            else:
                # Active mode: snapshot states
                states = await ha.states()
                lines = [f"{s['entity_id']}={s['state']}" for s in states[:400]]
                user = (
                    f"Mode: {mode}\n"
                    "Current states (subset):\n" + "\n".join(lines)
                )
                plan = gpt.complete_json(SYSTEM_ACTIVE, user, schema=ACTIONS_JSON_SCHEMA)
                summary = plan.get("text") or plan.get("summary") or "No summary."
                actions = plan.get("actions") or []
        finally:
            await ha.close()
    else:
        summary = f"Analysis in {mode} mode. Focus: {focus or 'General'}."
        actions = ["light.turn_off living_room", "climate.set_temperature bedroom 20°C"]

    row = db.add_analysis(mode, focus, summary, json.dumps(actions))
    return {"status": "ok", "summary": summary, "actions": actions, "row": row}

@app.get("/api/history")
def history():
    return db.get_analyses(50)

@app.get("/api/history/{analysis_id}")
def get_history_item(analysis_id: int):
    return db.get_analysis(analysis_id)

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

# ---------------- Helpers ----------------
def _load_config():
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return {"mode": "passive", "model": "gpt-4o-mini", "dry_run": True}

def _save_config(data):
    CONFIG_PATH.write_text(yaml.safe_dump(data))
