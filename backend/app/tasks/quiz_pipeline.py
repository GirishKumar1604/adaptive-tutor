from __future__ import annotations

import os
import json
from datetime import datetime
from typing import Any, Dict, Optional

from celery_app import celery
from db import session_scope
from models import JobStatus
from services.persistence_service import upsert_quiz_job_result

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/app/storage/videos")


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _make_public(quiz_full: Dict[str, Any]) -> Dict[str, Any]:
    """
    Strip answers / rationales etc. Keep only what frontend needs to display.
    """
    public_questions = []
    for q in quiz_full.get("questions", []) or []:
        public_questions.append(
            {
                "id": q.get("id"),
                "type": q.get("type"),
                "difficulty": q.get("difficulty"),
                "skill": q.get("skill"),
                "prompt": q.get("prompt"),
                "options": q.get("options"),
            }
        )
    return {"topic": quiz_full.get("topic", ""), "questions": public_questions}


@celery.task(name="tasks.quiz_pipeline.generate_quiz", bind=True)
def generate_quiz(
    self,
    *,
    job_id: str,
    topic: str,
    user_goal: Optional[str] = None,
    preferred_language: Optional[str] = "English",
    student_state: Optional[Dict[str, Any]] = None,
    num_questions: int = 8,
    rag_collection_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Quiz Pipeline:
      1) Read segments.json (must exist from learn pipeline)
      2) Generate quiz_full.json (includes correct answers + explanations)
      3) Generate quiz.json (public-safe for frontend)
    """
    from agents.quiz_agent import generate_quiz_full  # import inside for safe startup

    job_dir = os.path.join(OUTPUT_DIR, job_id)
    if not os.path.exists(job_dir):
        raise FileNotFoundError(f"job_id folder not found: {job_dir}")

    segments_path = os.path.join(job_dir, "segments.json")
    if not os.path.exists(segments_path):
        raise FileNotFoundError(f"segments.json not found at {segments_path}. Run learn pipeline first.")

    segments_obj = _load_json(segments_path)
    segments = segments_obj.get("segments", []) or []

    # ---- Retry wrapper (quick + reliable) ----
    last_err = None
    quiz_full = None
    rag_context = ""
    rag_sources = []
    rag_error = None
    if rag_collection_id:
        try:
            from services.rag_service import query_collection_with_sources

            rag_data = query_collection_with_sources(
                collection_id=rag_collection_id,
                question=f"{topic}\nGoal: {user_goal or ''}",
                top_k=5,
                max_chars=1400,
            )
            rag_context = rag_data.get("context") or ""
            rag_sources = rag_data.get("sources") or []
        except Exception as exc:
            rag_error = f"{type(exc).__name__}: {exc}"

    for _attempt in range(1, 4):
        try:
            quiz_full = generate_quiz_full(
                topic=topic,
                user_goal=user_goal or "Learn and test understanding.",
                preferred_language=preferred_language or "English",
                student_state=student_state or {},
                segments=segments,
                num_questions=num_questions,
                rag_context=rag_context,
            )
            break
        except Exception as e:
            last_err = e

    if quiz_full is None:
        raise RuntimeError(f"Quiz generation failed after retries: {last_err}")

    quiz_public = _make_public(quiz_full)

    quiz_full_path = os.path.join(job_dir, "quiz_full.json")
    quiz_path = os.path.join(job_dir, "quiz.json")

    _write_json(quiz_full_path, quiz_full)
    _write_json(quiz_path, quiz_public)

    payload = {
        "status": "COMPLETED",
        "job_id": job_id,
        "topic": topic,
        "quiz_path": quiz_path,
        "quiz_full_path": quiz_full_path,
        "rag_sources": rag_sources,
        "rag_error": rag_error,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }

    with session_scope() as db:
        status = JobStatus.SUCCESS if not rag_error else JobStatus.PARTIAL
        upsert_quiz_job_result(db, task_id=self.request.id, status=status, result_payload=payload)

    return payload
