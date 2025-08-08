from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import json
import yaml

# ---- Models (import or fallback) ----
try:
    from homegpt.api.models import AnalysisRequest, Settings  # if you created models.py
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

# ---- DB helpers ----
from homegpt.api import db

CONFIG_PATH = Path("/config/homegpt_config.yaml")

app = FastAPI(title="HomeGPT Dashboard API")

# CORS (adjust in prod)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure DB exists
db.init_db()

@app.get("/status")
def get_status():
    config = _load_config()
    last = db.get_analyses(1)
    return {
        "mode": config.get("mode", "passive"),
        "model": config.get("model", "gpt-4o-mini"),
        "dry_run": config.get("dry_run", True),
        "last_analysis": last[0] if last else None,
    }

@app.post("/mode")
def set_mode(mode: str):
    config = _load_config()
    config["mode"] = mode
    _save_config(config)
    return {"status": "ok", "mode": mode}

@app.post("/run")
def run_analysis(request: AnalysisRequest):
    # Placeholder AI; swap in your real analysis
    fake_summary = f"Analysis in {request.mode} mode. Focus: {request.focus or 'General'}."
    fake_actions = json.dumps([
        "light.turn_off living_room",
        "climate.set_temperature bedroom 20°C",
    ])
    db.add_analysis(request.mode, request.focus or "", fake_summary, fake_actions)
    return {"status": "ok", "summary": fake_summary, "actions": json.loads(fake_actions)}

@app.get("/history")
def history():
    return db.get_analyses(50)

@app.get("/history/{analysis_id}")
def get_history_item(analysis_id: int):
    return db.get_analysis(analysis_id)

@app.get("/settings")
def get_settings():
    return _load_config()

@app.post("/settings")
def update_settings(settings: Settings):
    # merge with existing config, keep unspecified keys
    cfg = _load_config()
    data = {k: v for k, v in settings.dict().items() if v is not None}
    cfg.update(data)
    _save_config(cfg)
    return {"status": "ok"}

# ------------- helpers -------------
def _load_config():
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return {"mode": "passive", "model": "gpt-4o-mini", "dry_run": True}

def _save_config(data):
    CONFIG_PATH.write_text(yaml.safe_dump(data))
