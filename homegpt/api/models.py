"""
Pydantic models used by the HomeGPT API.

Notes:
- Settings fields are optional so /api/settings can accept partial updates.
- EventFeedbackIn accepts a legacy 'feedback' alias for 'note'.
"""

from typing import Optional, List
from pydantic import BaseModel, root_validator, Field


# ---------- Analysis requests / responses ----------

class AnalysisRequest(BaseModel):
    """Trigger a new analysis. 'mode' is optional; backend chooses default if omitted."""
    mode: Optional[str] = None
    focus: Optional[str] = None


class AnalysisSummary(BaseModel):
    """Describes a stored analysis (if you ever serialize to this)."""
    id: int
    # If you ever hydrate from DB rows with 'ts', this alias keeps it compatible:
    timestamp: str = Field(..., alias="ts")
    mode: str
    focus: Optional[str]
    summary: str

    class Config:
        allow_population_by_field_name = True


class AnalysisListItem(BaseModel):
    """Lightweight history row."""
    id: int
    timestamp: str = Field(..., alias="ts")
    mode: str
    focus: Optional[str]
    summary: str

    class Config:
        allow_population_by_field_name = True


# ---------- Settings (all optional for PATCH-like behavior) ----------

class Settings(BaseModel):
    """
    Mirrors options consumed in main.py/_load_config():
    - Keep every field Optional so missing values mean "leave unchanged".
    """
    openai_api_key: Optional[str] = None
    model: Optional[str] = None
    mode: Optional[str] = None
    summarize_time: Optional[str] = None
    control_allowlist: Optional[List[str]] = None
    max_actions_per_hour: Optional[int] = None
    dry_run: Optional[bool] = None
    log_level: Optional[str] = None
    language: Optional[str] = None

    # History / compression tuning used in main.py
    history_hours: Optional[int] = None
    history_max_lines: Optional[int] = None
    history_jitter_sec: Optional[int] = None
    history_all_max_entities: Optional[int] = None
    history_chunk_size: Optional[int] = None


# ---------- Follow-ups ----------

class FollowupRunRequest(BaseModel):
    analysis_id: int
    code: str


# ---------- Feedback ----------

class FeedbackUpdate(BaseModel):
    note: Optional[str] = None
    kind: Optional[str] = None  # e.g., "context", "correction", "preference"


class EventFeedbackIn(BaseModel):
    """
    Create feedback. Server requires a 'note' (it can resolve event by event_id,
    or by (analysis_id + body) if event_id is omitted).
    """
    event_id: Optional[int] = None
    analysis_id: Optional[int] = None
    body: Optional[str] = None
    category: Optional[str] = None
    note: Optional[str] = None           # canonical
    kind: Optional[str] = "context"

    # Accept legacy 'feedback' as an alias for 'note'
    @root_validator(pre=True)
    def _map_feedback_alias(cls, v):
        if v is None:
            return v
        if not v.get("note") and v.get("feedback"):
            v["note"] = v["feedback"]
        return v
