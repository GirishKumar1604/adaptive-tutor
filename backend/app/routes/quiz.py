from __future__ import annotations

import os
import json
from datetime import datetime
from typing import Optional, Any, Dict, List, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from celery_app import celery

router = APIRouter()

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/app/storage/videos")

# ✅ Must match adaptive routes
SESSIONS_DIR = os.path.join(OUTPUT_DIR, "_sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)


# -------------------------
# Models
# -------------------------
class QuizStartRequest(BaseModel):
    job_id: str = Field(..., min_length=6)
    topic: str = Field(..., min_length=2)
    user_goal: Optional[str] = None
    preferred_language: Optional[str] = "English"
    num_questions: int = 8


class QuizStartResponse(BaseModel):
    job_id: str
    task_id: str


class QuizAnswer(BaseModel):
    question_id: str
    answer: str


class QuizSubmitRequest(BaseModel):
    # ✅ adaptive loop will pass this (optional)
    session_id: Optional[str] = None

    job_id: str = Field(..., min_length=6)
    topic: str = Field(..., min_length=2)
    preferred_language: Optional[str] = "English"
    answers: List[QuizAnswer]


# -------------------------
# Helpers
# -------------------------
def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _session_path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")


def _load_session(session_id: str) -> Dict[str, Any]:
    path = _session_path(session_id)
    if not os.path.exists(path):
        raise FileNotFoundError(f"session_id not found: {session_id}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_session(session_id: str, data: Dict[str, Any]) -> None:
    path = _session_path(session_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _skill_key(topic: str, skill: str) -> str:
    """
    Stores mastery as "<Topic>::<Skill>".
    If skill already contains "::", keep it as-is.
    """
    t = (topic or "Topic").strip()
    s = (skill or "Core").strip()
    if "::" in s:
        return s
    return f"{t}::{s}"


def _difficulty_from_avg(avg: float) -> str:
    # MUST match adaptive.py mapping
    if avg >= 0.80:
        return "HARD"
    if avg >= 0.55:
        return "MEDIUM"
    return "EASY"


def _ensure_summary(graded: Dict[str, Any]) -> Dict[str, Any]:
    if graded.get("summary") is not None:
        return graded

    results = graded.get("results") or []
    total = len(results) if isinstance(results, list) else 0
    correct = 0

    if isinstance(results, list):
        for r in results:
            if r.get("correct") is True:
                correct += 1

    incorrect = max(0, total - correct)
    score_percent = round((correct / total) * 100, 2) if total else 0.0

    graded["summary"] = {
        "total_questions": total,
        "correct": correct,
        "incorrect": incorrect,
        "score_percent": score_percent,
    }
    return graded


def _adaptive_summary_from_session(state: Dict[str, Any]) -> Dict[str, Any]:
    mastery = state.get("mastery") or {}
    if not mastery:
        return {
            "avg_mastery": None,
            "difficulty_level": state.get("difficulty_level"),
            "weakest_skills": [],
        }

    vals = [float(v) for v in mastery.values() if isinstance(v, (int, float))]
    avg = round(sum(vals) / len(vals), 4) if vals else None

    weakest = sorted(mastery.items(), key=lambda kv: float(kv[1]))[:3]
    weakest_skills = []
    for k, _v in weakest:
        weakest_skills.append(k.split("::", 1)[1] if "::" in k else k)

    return {
        "avg_mastery": avg,
        "difficulty_level": state.get("difficulty_level"),
        "weakest_skills": weakest_skills,
    }


def _compute_deltas_by_difficulty(difficulty: str) -> Tuple[float, float]:
    """
    Returns (delta_if_correct, delta_if_wrong)
    Tune later — this is MVP stable.
    """
    d = (difficulty or "").upper()
    if d == "HARD":
        return (0.08, -0.10)
    if d == "MEDIUM":
        return (0.06, -0.08)
    # EASY default
    return (0.04, -0.06)


def _update_mastery_from_attempt(
    *,
    state: Dict[str, Any],
    quiz_full: Dict[str, Any],
    answers: List[Dict[str, Any]],
    graded_results: List[Dict[str, Any]],
) -> None:
    """
    Updates state["mastery"] per skill using quiz_full + graded results.
    Storage format:
      mastery["<Topic>::<Skill>"] = float in [0, 1]
    """
    mastery = state.get("mastery") or {}
    state["mastery"] = mastery

    topic = (state.get("topic") or "").strip() or "Topic"

    # Build question lookup from quiz_full
    qmap = {q.get("id"): q for q in (quiz_full.get("questions") or []) if q.get("id")}

    # Map correctness by question_id
    correct_map: Dict[str, bool] = {}
    for r in graded_results or []:
        qid = r.get("question_id")
        if qid:
            correct_map[qid] = bool(r.get("correct") is True)

    # Update each answered question
    for a in answers or []:
        qid = a.get("question_id")
        if not qid:
            continue

        q = qmap.get(qid)
        if not q:
            continue

        raw_skill = (q.get("skill") or "Core").strip()
        difficulty = (q.get("difficulty") or "EASY").upper()

        key = _skill_key(topic, raw_skill)

        old = float(mastery.get(key, 0.5))
        dc, dw = _compute_deltas_by_difficulty(difficulty)

        new = old + (dc if correct_map.get(qid, False) else dw)
        mastery[key] = round(_clamp01(new), 4)

    # Update overall difficulty_level from avg mastery (aligned with adaptive.py)
    vals = [float(v) for v in mastery.values() if isinstance(v, (int, float))]
    avg = sum(vals) / len(vals) if vals else 0.5
    state["difficulty_level"] = _difficulty_from_avg(avg)


# -------------------------
# Routes
# -------------------------
@router.post("/quiz/start", response_model=QuizStartResponse)
def quiz_start(req: QuizStartRequest):
    try:
        async_result = celery.send_task(
            "tasks.quiz_pipeline.generate_quiz",
            kwargs={
                "job_id": req.job_id,
                "topic": req.topic,
                "user_goal": req.user_goal,
                "preferred_language": req.preferred_language or "English",
                "student_state": {},
                "num_questions": req.num_questions,
            },
        )
        return QuizStartResponse(job_id=req.job_id, task_id=async_result.id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to start quiz: {type(e).__name__}: {e}")


@router.get("/quiz/status/{task_id}")
def quiz_status(task_id: str):
    res = celery.AsyncResult(task_id)

    if res.state in ("PENDING", "STARTED", "RETRY"):
        return {"state": res.state}

    if res.state == "FAILURE":
        return {"state": "FAILURE", "error": str(res.info)}

    return {"state": "SUCCESS", "result": res.result}


@router.get("/quiz/get/{job_id}")
def quiz_get(job_id: str):
    job_dir = os.path.join(OUTPUT_DIR, job_id)
    quiz_path = os.path.join(job_dir, "quiz.json")

    if not os.path.exists(quiz_path):
        raise HTTPException(status_code=404, detail="quiz.json not found. Run /quiz/start first.")

    return _load_json(quiz_path)


@router.post("/quiz/submit")
def quiz_submit(req: QuizSubmitRequest):
    """
    Grades against quiz_full.json generated by quiz pipeline.
    If session_id is provided, also updates adaptive session mastery + difficulty.
    """
    try:
        from agents.quiz_agent import grade_quiz  # import here for safer startup

        job_dir = os.path.join(OUTPUT_DIR, req.job_id)
        quiz_full_path = os.path.join(job_dir, "quiz_full.json")

        if not os.path.exists(quiz_full_path):
            raise HTTPException(status_code=404, detail="quiz_full.json not found. Run /quiz/start first.")

        quiz_full = _load_json(quiz_full_path)

        graded = grade_quiz(
            topic=req.topic,
            student_state={},
            preferred_language=req.preferred_language or "English",
            quiz_full=quiz_full,
            answers=[a.model_dump() for a in req.answers],
        )

        graded = _ensure_summary(graded)

        # ✅ Adaptive update
        if req.session_id:
            try:
                state = _load_session(req.session_id)
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail=f"session_id not found: {req.session_id}")

            _update_mastery_from_attempt(
                state=state,
                quiz_full=quiz_full,
                answers=[a.model_dump() for a in req.answers],
                graded_results=graded.get("results") or [],
            )

            state["updated_at"] = datetime.utcnow().isoformat() + "Z"
            _write_session(req.session_id, state)

            graded["adaptive"] = {
                "session_id": req.session_id,
                "student_state": state,
                "adaptive_summary": _adaptive_summary_from_session(state),
            }

        return graded

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to grade quiz: {type(e).__name__}: {e}")
