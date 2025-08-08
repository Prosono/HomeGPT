from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import json
import yaml

# Absolute imports for package safety
from homegpt.api.models import AnalysisRequest, Settings
from homegpt.api.add_analysis import add_analysis
from homegpt.api.get_analyses import get_analyses
from homegpt.api.get_analysis import get_analysis

CONFIG_PATH = Path("/config/homegpt_config.yaml")

app = FastAPI(title="HomeGPT Dashboard API")

# Enable CORS (adjust in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/status")
def get_status():
    """Return current mode, model, dry_run setting, and last analysis."""
    config = _load_config()
    return {
        "mode": config["mode"],
        "model": config["model"],
        "dry_run": config["dry_run"],
        "last_analysis": get_analyses(1)[0] if get_analyses(1) else None
    }

@app.post("/mode")
def set_mode(mode: str):
    """Set operation mode."""
    config = _load_config()
    config["mode"] = mode
    _save_config(config)
    return {"status": "ok", "mode": mode}

@app.post("/run")
def run_analysis(request: AnalysisRequest):
    """
    Trigger an analysis.  
    This is where AI logic would be integrated.
    """
    fake_summary = f"Analysis in {request.mode} mode. Focus: {request.focus or 'General'}."
    fake_actions = json.dumps([
        "light.turn_off living_room",
        "climate.set_temperature bedroom 20°C"
    ])
    add_analysis(request.mode, request.focus or "", fake_summary, fake_actions)
    return {
        "status": "ok",
        "summary": fake_summary,
        "actions": json.loads(fake_actions)
    }

@app.get("/history")
def history():
    """Return last 50 analyses."""
    return get_analyses(50)

@app.get("/history/{analysis_id}")
def get_history_item(analysis_id: int):
    """Return a specific analysis by ID."""
    return get_analysis(analysis_id)

@app.get("/settings")
def get_settings():
    """Return current settings."""
    return _load_config()

@app.post("/settings")
def update_settings(settings: Settings):
    """Update settings."""
    _save_config(settings.dict())
    return {"status": "ok"}

# -----------------------
# Internal helpers
# -----------------------

def _load_config():
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text())
    return {"mode": "passive", "model": "gpt-4o-mini", "dry_run": True}

def _save_config(data):
    CONFIG_PATH.write_text(yaml.safe_dump(data))
