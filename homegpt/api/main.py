from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .models import AnalysisRequest, Settings
from . import add_analysis, get_analyses, get_analysis
import json
from pathlib import Path
import yaml
from .add_analysis import add_analysis
from .get_analyses import get_analyses
from .get_analysis import get_analysis


CONFIG_PATH = Path("/config/homegpt_config.yaml")

app = FastAPI(title="HomeGPT Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/status")
def get_status():
    config = _load_config()
    return {
        "mode": config["mode"],
        "model": config["model"],
        "dry_run": config["dry_run"],
        "last_analysis": get_analyses(1)[0] if get_analyses(1) else None
    }

@app.post("/mode")
def set_mode(mode: str):
    config = _load_config()
    config["mode"] = mode
    _save_config(config)
    return {"status": "ok", "mode": mode}

@app.post("/run")
def run_analysis(request: AnalysisRequest):
    # This is where you'd integrate your AI
    fake_summary = f"Analysis in {request.mode} mode. Focus: {request.focus or 'General'}."
    fake_actions = json.dumps(["light.turn_off living_room", "climate.set_temperature bedroom 20°C"])
    add_analysis(request.mode, request.focus or "", fake_summary, fake_actions)
    return {"status": "ok", "summary": fake_summary, "actions": json.loads(fake_actions)}

@app.get("/history")
def history():
    return get_analyses(50)

@app.get("/history/{analysis_id}")
def get_history_item(analysis_id: int):
    return get_analysis(analysis_id)

@app.get("/settings")
def get_settings():
    return _load_config()

@app.post("/settings")
def update_settings(settings: Settings):
    _save_config(settings.dict())
    return {"status": "ok"}

def _load_config():
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text())
    return {"mode": "passive", "model": "gpt-4o-mini", "dry_run": True}

def _save_config(data):
    CONFIG_PATH.write_text(yaml.safe_dump(data))
