from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOCAL_STATE_DIR = _REPO_ROOT / ".homegpt"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_json_list(name: str) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [str(item) for item in data]
    return []


def get_data_dir() -> Path:
    custom = os.getenv("HOMEGPT_DATA_DIR")
    if custom:
        return Path(custom).expanduser()
    if Path("/data").exists():
        return Path("/data")
    return _LOCAL_STATE_DIR


def get_db_path() -> Path:
    custom = os.getenv("HOMEGPT_DB")
    if custom:
        return Path(custom).expanduser()
    return get_data_dir() / "homegpt.db"


def get_config_path() -> Path:
    custom = os.getenv("HOMEGPT_CONFIG")
    if custom:
        return Path(custom).expanduser()
    if Path("/config").exists():
        return Path("/config/homegpt_config.yaml")
    return get_data_dir() / "homegpt_config.yaml"


def load_persisted_config() -> dict[str, Any]:
    path = get_config_path()
    if not path.exists():
        return {}
    try:
        raw = path.read_text()
        if yaml is not None:
            data = yaml.safe_load(raw) or {}
        else:
            data = json.loads(raw or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_persisted_config(data: dict[str, Any]) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if yaml is not None:
        path.write_text(yaml.safe_dump(data, sort_keys=False))
    else:
        path.write_text(json.dumps(data, indent=2))


def load_runtime_settings() -> dict[str, Any]:
    settings: dict[str, Any] = {
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "model": os.getenv("MODEL") or os.getenv("OPENAI_MODEL") or "gpt-5",
        "mode": os.getenv("MODE", "passive"),
        "summarize_time": os.getenv("SUMMARIZE_TIME", "21:30"),
        "control_allowlist": _env_json_list("CONTROL_ALLOWLIST"),
        "max_actions_per_hour": _env_int("MAX_ACTIONS_PER_HOUR", 10),
        "dry_run": _env_bool("DRY_RUN", True),
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
        "language": os.getenv("LANGUAGE", "en"),
    }
    overrides = load_persisted_config()
    settings.update({key: value for key, value in overrides.items() if value is not None})
    return settings
