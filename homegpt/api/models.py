"""
Pydantic models used by the HomeGPT API.

This version makes the `mode` field optional so that requests from the
frontend don't fail if the caller omits a mode.  The handler in
``homegpt/api/main.py`` will fill in a default using the current
configuration.
"""

from pydantic import BaseModel
from typing import Optional, List


class AnalysisRequest(BaseModel):
    """Request body for triggering a new analysis.

    ``mode`` is optional; if not provided the API will fall back to the
    configured default.  ``focus`` can optionally supply a focus topic.
    """

    mode: Optional[str] = None
    focus: Optional[str] = None


class AnalysisSummary(BaseModel):
    """Model describing a previously stored analysis."""

    id: int
    timestamp: str
    mode: str
    focus: Optional[str]
    summary: str


class AnalysisListItem(BaseModel):
    """Lightweight representation of an analysis for history listings."""

    id: int
    timestamp: str
    mode: str
    focus: Optional[str]
    summary: str


class Settings(BaseModel):
    """User‑configurable settings for HomeGPT.

    These fields mirror the options exposed in ``config.yaml``.  When
    updating settings via the API, unspecified fields will be left
    unchanged.
    """

    mode: str
    model: str
    dry_run: bool

class FollowupRunRequest(BaseModel):
    analysis_id: int
    code: str    