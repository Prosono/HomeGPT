"""
HomeGPT Dashboard API package.

This package handles:
- Database setup and access
- Pydantic models for API requests/responses
- FastAPI app initialization
"""

from . import db
from .models import AnalysisRequest, Settings, FollowupRunRequest, EventFeedbackIn


# Initialize database on package import
db.init_db()

# Expose common objects for easier imports
from .db import add_analysis, get_analyses, get_analysis

__all__ = [
    "add_analysis",
    "get_analyses",
    "get_analysis",
    "AnalysisRequest",
    "Settings",
]
