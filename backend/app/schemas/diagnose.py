from pydantic import BaseModel, Field
from typing import List, Literal, Optional

Difficulty = Literal["EASY", "MEDIUM", "HARD"]
SkillTag = Literal["definition", "intuition", "application", "prereq", "preference"]

LearningLevel = Literal["Beginner", "Intermediate", "Advanced"]
Pace = Literal["Slow", "Normal", "Fast"]
Style = Literal["Story", "Formal", "ExampleDriven"]


class DiagnoseQuestion(BaseModel):
    id: str
    topic: str
    difficulty: Difficulty
    skill: SkillTag
    prompt: str


class StartSessionRequest(BaseModel):
    topic: str = Field(..., min_length=2)
    user_goal: Optional[str] = None
    preferred_language: Optional[str] = "English"


class StartSessionResponse(BaseModel):
    session_id: str
    questions: List[DiagnoseQuestion]


class DiagnoseAnswer(BaseModel):
    question_id: str
    answer_text: str
    confidence_1to5: int = Field(..., ge=1, le=5)


class SubmitDiagnoseRequest(BaseModel):
    session_id: str
    topic: str
    answers: List[DiagnoseAnswer]


class Gap(BaseModel):
    tag: str
    description: str


class StudentState(BaseModel):
    level: LearningLevel
    pace: Pace
    style: Style

    # safe defaults so LLM omissions don't crash validation
    theta: float = Field(0.0, ge=-2.0, le=2.0)
    confidence: float = Field(0.5, ge=0.0, le=1.0)

    # avoid mutable default lists
    gaps: List[Gap] = Field(default_factory=list)
    needs_prereqs: List[str] = Field(default_factory=list)


class SubmitDiagnoseResponse(BaseModel):
    student_state: StudentState
    story_explanation: str
    learn_job_id: Optional[str] = None
    learn_task_id: Optional[str] = None
