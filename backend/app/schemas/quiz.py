from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Dict, Any

Difficulty = Literal["EASY", "MEDIUM", "HARD"]
QuestionType = Literal["mcq", "short"]
SkillTag = Literal["definition", "intuition", "application", "prereq", "preference"]


class QuizOption(BaseModel):
    id: str  # "A", "B", "C", "D"
    text: str


class QuizQuestionPublic(BaseModel):
    id: str  # "Q1".."Qn"
    type: QuestionType
    difficulty: Difficulty
    skill: SkillTag
    prompt: str
    options: Optional[List[QuizOption]] = None  # mcq only; short => null


class QuizPublic(BaseModel):
    topic: str
    questions: List[QuizQuestionPublic]


class QuizQuestionFull(QuizQuestionPublic):
    correct_answer: str  # "A"/"B"/"C"/"D" for mcq, short expected text for short
    explanation: str


class QuizFull(BaseModel):
    topic: str
    questions: List[QuizQuestionFull]


class QuizStartRequest(BaseModel):
    job_id: str = Field(..., min_length=6)
    topic: str = Field(..., min_length=2)
    user_goal: Optional[str] = None
    preferred_language: Optional[str] = "English"
    student_state: Optional[Dict[str, Any]] = None
    num_questions: int = Field(8, ge=5, le=12)


class QuizStartResponse(BaseModel):
    job_id: str
    task_id: str


class QuizAnswer(BaseModel):
    question_id: str
    answer: str


class QuizSubmitRequest(BaseModel):
    job_id: str
    topic: str
    preferred_language: Optional[str] = "English"
    student_state: Optional[Dict[str, Any]] = None
    answers: List[QuizAnswer] = Field(default_factory=list)
    grade_only_attempted: bool = False  # false => enforce results for ALL questions


class PerQuestionResult(BaseModel):
    question_id: str
    correct: bool
    score: float = Field(..., ge=0.0, le=1.0)
    feedback: str


class QuizSubmitResponse(BaseModel):
    overall_score: float = Field(..., ge=0.0, le=1.0)
    summary: str
    results: List[PerQuestionResult]
