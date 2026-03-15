from __future__ import annotations

from pydantic import BaseModel, Field
from typing import List, Optional, Literal

from schemas.diagnose import StudentState


class LearnStartRequest(BaseModel):
    topic: str = Field(..., min_length=2)
    user_goal: Optional[str] = None
    preferred_language: Optional[str] = "English"

    # optional: pass the StudentState you got from /diagnose/submit
    student_state: Optional[StudentState] = None

    # video quality for manim
    quality: Literal["low", "medium"] = "low"


class LearnStartResponse(BaseModel):
    job_id: str
    task_id: str


class LearnStatusResponse(BaseModel):
    state: str
    result: Optional[dict] = None
    error: Optional[str] = None
