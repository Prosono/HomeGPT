from fastapi import FastAPI, APIRouter, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import logging, json, yaml

# ---- Models (fallback if models.py missing)
try:
    from homegpt.api.models import AnalysisRequest, Settings
except Exception:
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

from homegpt.api import db

CONFIG_PATH = Path("/config/homegpt_config.yaml")

# Find frontend in api/frontend or ../frontend
candidates = [Path(__file__).parent / "frontend", Path(__file__).parent.parent / "frontend"]
for p in candidates:
    if (p / "index.html").exists():
        FRONTEND_DIR = p
        break
else:
    FRONTEND_DIR = candidates[0]

app = FastAPI(title="HomeGPT Dashboard API")

# Simple request logging so you can see if the JS is hitting the API
logging.basicConfig(level=logging.INFO)
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logging.info("→ %s %s", request.method, request.url)
    resp = await call_next(request)
    logging.info("← %s %s", resp.status_code, request.url.path)
    return resp

# CORS not strictly needed under ingress, but harmless
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# Ensure DB exists
db.init_db()

# ---------- Frontend ----------
@app.get("/")
async def root():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"error": f"Dashboard not found at {index_file}"}

app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")

# ---------- API (under /api) ----------
api = APIRouter()

@api.get("/status")
def get_status():
    cfg = _load_config()
    last = db.get_analyses(1)
    return {
        "mode": cfg.get("mode", "passive"),
        "model": cfg.get("model", "gpt-4o-mini"),
        "dry_run": cfg.get("dry_run", True),
        "last_analysis": last[0] if last else None,
    }

@api.post("/mode")
def set_mode(mode: str):
    cfg = _load_config()
    cfg["mode"] = mode
    _save_config(cfg)
    return {"status": "ok", "mode": mode}

@api.post("/run")
def run_analysis(request: AnalysisRequest):
    fake_summary = f"Analysis in {request.mode} mode. Focus: {request.focus or 'General'}."
    fake_actions = json.dumps(["light.turn_off living_room", "climate.set_temperature bedroom 20°C"])
    db.add_analysis(request.mode, request.focus or "", fake_summary, fake_actions)
    return {"status": "ok", "summary": fake_summary, "actions": json.loads(fake_actions)}

@api.get("/history")
def history():
    return db.get_analyses(50)

@api.get("/history/{analysis_id}")
def get_history_item(analysis_id: int):
    return db.get_analysis(analysis_id)

@api.get("/settings")
def get_settings():
    return _load_config()

@api.post("/settings")
def update_settings(settings: Settings):
    cfg = _load_config()
    data = {k: v for k, v in settings.dict().items() if v is not None}
    cfg.update(data)
    _save_config(cfg)
    return {"status": "ok"}

app.include_router(api, prefix="/api")

# ---------- helpers ----------
def _load_config():
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return {"mode": "passive", "model": "gpt-4o-mini", "dry_run": True}

def _save_config(data):
    CONFIG_PATH.write_text(yaml.safe_dump(data))
