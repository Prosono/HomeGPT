from pydantic import BaseModel
from typing import Optional, List

class AnalysisRequest(BaseModel):
    mode: str
    focus: Optional[str] = None

class AnalysisSummary(BaseModel):
    id: int
    timestamp: str
    mode: str
    focus: Optional[str]
    summary: str

class AnalysisListItem(BaseModel):
    id: int
    timestamp: str
    mode: str
    focus: Optional[str]
    summary: str

class Settings(BaseModel):
    mode: str
    model: str
    dry_run: bool
