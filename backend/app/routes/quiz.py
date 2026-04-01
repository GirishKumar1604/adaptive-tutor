from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from celery_app import celery
from db import SessionLocal
from models import JobStatus
from services.api_response import fail, ok
from services.mastery_service import avg_mastery, update_mastery_from_quiz, weakest_skills
from services.persistence_service import (
    add_mastery_snapshot,
    create_or_get_quiz_attempt,
    create_quiz_job,
    get_session_or_404,
    save_session_state,
    upsert_quiz_job_result,
)
from services.rag_service import query_collection_with_sources

router = APIRouter()

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/app/storage/videos")


class QuizStartRequest(BaseModel):
    session_id: Optional[str] = None
    job_id: str = Field(..., min_length=6)
    topic: str = Field(..., min_length=2)
    user_goal: Optional[str] = None
    preferred_language: Optional[str] = "English"
    num_questions: int = 8


class QuizAnswer(BaseModel):
    question_id: str
    answer: str


class QuizSubmitRequest(BaseModel):
    session_id: Optional[str] = None
    attempt_no: int = 1
    job_id: str = Field(..., min_length=6)
    topic: str = Field(..., min_length=2)
    preferred_language: Optional[str] = "English"
    answers: List[QuizAnswer]


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _ensure_summary(graded: Dict[str, Any]) -> Dict[str, Any]:
    if graded.get("summary") is not None:
        return graded
    results = graded.get("results") or []
    total = len(results)
    correct = sum(1 for r in results if r.get("correct") is True)
    graded["summary"] = {
        "total_questions": total,
        "correct": correct,
        "incorrect": max(0, total - correct),
        "score_percent": round((correct / total) * 100, 2) if total else 0.0,
    }
    return graded


@router.post("/quiz/start")
def quiz_start(req: QuizStartRequest):
    try:
        rag_collection_id = None
        with SessionLocal() as db:
            if req.session_id:
                session = get_session_or_404(db, req.session_id)
                if not session:
                    return fail(error="session_id not found")
                rag_collection_id = session.rag_collection_id

        async_result = celery.send_task(
            "tasks.quiz_pipeline.generate_quiz",
            kwargs={
                "job_id": req.job_id,
                "topic": req.topic,
                "user_goal": req.user_goal,
                "preferred_language": req.preferred_language or "English",
                "student_state": {},
                "num_questions": req.num_questions,
                "rag_collection_id": rag_collection_id,
            },
        )

        with SessionLocal() as db:
            create_quiz_job(
                db,
                job_id=req.job_id,
                task_id=async_result.id,
                topic=req.topic,
                session_id=req.session_id,
            )
            db.commit()
        return ok(result={"job_id": req.job_id, "task_id": async_result.id, "session_id": req.session_id}, state="PENDING")
    except Exception as e:
        return fail(error=f"Failed to start quiz: {type(e).__name__}: {e}")


@router.get("/quiz/status/{task_id}")
def quiz_status(task_id: str):
    res = celery.AsyncResult(task_id)
    with SessionLocal() as db:
        if res.state in ("PENDING", "STARTED", "RETRY"):
            upsert_quiz_job_result(
                db,
                task_id=task_id,
                status=JobStatus.RUNNING if res.state == "STARTED" else JobStatus.PENDING,
                result_payload={},
            )
            db.commit()
            return ok(state=res.state, result=None)

        if res.state == "FAILURE":
            upsert_quiz_job_result(db, task_id=task_id, status=JobStatus.FAILURE, result_payload={"error": str(res.info)})
            db.commit()
            return fail(state="FAILURE", error=str(res.info))

        payload = res.result or {}
        status = JobStatus.PARTIAL if payload.get("rag_error") else JobStatus.SUCCESS
        upsert_quiz_job_result(db, task_id=task_id, status=status, result_payload=payload)
        db.commit()
        warnings = [payload.get("rag_error")] if payload.get("rag_error") else []
        return ok(state=status.value, result=payload, warnings=warnings)


@router.get("/quiz/get/{job_id}")
def quiz_get(job_id: str):
    quiz_path = os.path.join(OUTPUT_DIR, job_id, "quiz.json")
    if not os.path.exists(quiz_path):
        return fail(error="quiz.json not found. Run /quiz/start first.")
    return ok(result=_read_json(quiz_path))


@router.post("/quiz/submit")
def quiz_submit(req: QuizSubmitRequest):
    try:
        from agents.quiz_agent import grade_quiz

        job_dir = os.path.join(OUTPUT_DIR, req.job_id)
        quiz_full_path = os.path.join(job_dir, "quiz_full.json")
        if not os.path.exists(quiz_full_path):
            return fail(error="quiz_full.json not found. Run /quiz/start first.")

        quiz_full = _read_json(quiz_full_path)
        graded = grade_quiz(
            topic=req.topic,
            student_state={},
            preferred_language=req.preferred_language or "English",
            quiz_full=quiz_full,
            answers=[a.model_dump() for a in req.answers],
        )
        graded = _ensure_summary(graded)

        warnings: list[str] = []
        rag_sources = []
        with SessionLocal() as db:
            if req.session_id:
                session = get_session_or_404(db, req.session_id)
                if not session:
                    return fail(error=f"session_id not found: {req.session_id}")

                attempt, created = create_or_get_quiz_attempt(
                    db,
                    session_id=req.session_id,
                    job_id=req.job_id,
                    attempt_no=req.attempt_no,
                    answers_payload={"answers": [a.model_dump() for a in req.answers]},
                    graded_payload=graded,
                )
                if not created:
                    db.commit()
                    return ok(result=attempt.graded_payload, state="SUCCESS")

                state = {
                    "topic": session.topic,
                    "mastery": session.mastery or {},
                    "difficulty_level": session.difficulty_level,
                    "student_state": session.student_state or {},
                }
                state = update_mastery_from_quiz(
                    state=state,
                    quiz_full=quiz_full,
                    answers=[a.model_dump() for a in req.answers],
                    graded_results=graded.get("results") or [],
                )
                save_session_state(
                    db,
                    session,
                    {
                        "mastery": state["mastery"],
                        "difficulty_level": state["difficulty_level"],
                        "student_state": state["student_state"],
                        "last_action": "QUIZ_SUBMIT",
                        "last_quiz_task_id": session.last_quiz_task_id,
                        "last_remediation_path": session.last_remediation_path,
                    },
                )
                add_mastery_snapshot(
                    db,
                    session_id=session.id,
                    mastery=state["mastery"],
                    difficulty_level=state["difficulty_level"],
                )

                if session.rag_collection_id:
                    try:
                        rag = query_collection_with_sources(
                            collection_id=session.rag_collection_id,
                            question=f"Provide grounding feedback for {req.topic}",
                            top_k=3,
                            max_chars=600,
                        )
                        rag_sources = rag.get("sources") or []
                    except Exception as exc:
                        warnings.append(f"RAG feedback fallback: {type(exc).__name__}: {exc}")

                graded["adaptive"] = {
                    "session_id": req.session_id,
                    "student_state": {
                        "mastery": state["mastery"],
                        "difficulty_level": state["difficulty_level"],
                        "updated_at": datetime.utcnow().isoformat() + "Z",
                    },
                    "adaptive_summary": {
                        "avg_mastery": round(avg_mastery(state["mastery"]), 4),
                        "difficulty_level": state["difficulty_level"],
                        "weakest_skills": weakest_skills(state["mastery"]),
                    },
                }
                if rag_sources:
                    graded["provenance"] = {"rag_sources": rag_sources}

            db.commit()

        return ok(result=graded, warnings=warnings)
    except Exception as e:
        return fail(error=f"Failed to grade quiz: {type(e).__name__}: {e}")
