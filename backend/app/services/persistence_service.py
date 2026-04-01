from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import (
    AdaptiveTransition,
    JobStatus,
    LessonJob,
    MasterySnapshot,
    QuizAttempt,
    QuizJob,
    RagChunk,
    RagCollection,
    RemediationPack,
    SessionModel,
)
from services.mastery_service import avg_mastery


def get_session_or_404(db: Session, session_id: str) -> SessionModel | None:
    return db.get(SessionModel, session_id)


def create_session(
    db: Session,
    *,
    session_id: str,
    job_id: str,
    topic: str,
    preferred_language: str,
    rag_collection_id: Optional[str],
    mastery: Dict[str, float],
    student_state: Dict[str, Any],
    difficulty_level: str,
) -> SessionModel:
    row = SessionModel(
        id=session_id,
        topic=topic,
        preferred_language=preferred_language,
        rag_collection_id=rag_collection_id,
        job_id=job_id,
        mastery=mastery,
        student_state=student_state,
        difficulty_level=difficulty_level,
    )
    db.add(row)
    db.flush()
    return row


def save_session_state(db: Session, session: SessionModel, state: Dict[str, Any]) -> SessionModel:
    session.mastery = state.get("mastery") or {}
    session.student_state = state.get("student_state") or {}
    session.difficulty_level = state.get("difficulty_level") or session.difficulty_level
    session.last_action = state.get("last_action")
    session.last_quiz_task_id = state.get("last_quiz_task_id")
    session.last_remediation_path = state.get("last_remediation_path")
    session.updated_at = datetime.utcnow()
    db.add(session)
    db.flush()
    return session


def create_lesson_job(
    db: Session,
    *,
    job_id: str,
    task_id: str,
    topic: str,
    session_id: Optional[str] = None,
) -> LessonJob:
    row = LessonJob(job_id=job_id, task_id=task_id, topic=topic, session_id=session_id, status=JobStatus.PENDING)
    db.add(row)
    db.flush()
    return row


def upsert_lesson_job_result(db: Session, *, task_id: str, status: JobStatus, result_payload: Dict[str, Any], warning: Optional[str]) -> None:
    row = db.execute(select(LessonJob).where(LessonJob.task_id == task_id)).scalar_one_or_none()
    if row is None:
        return
    row.status = status
    row.result_payload = result_payload
    row.warning = warning
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.flush()


def create_quiz_job(
    db: Session,
    *,
    job_id: str,
    task_id: str,
    topic: str,
    session_id: Optional[str] = None,
) -> QuizJob:
    row = QuizJob(job_id=job_id, task_id=task_id, topic=topic, session_id=session_id, status=JobStatus.PENDING)
    db.add(row)
    db.flush()
    return row


def upsert_quiz_job_result(db: Session, *, task_id: str, status: JobStatus, result_payload: Dict[str, Any]) -> None:
    row = db.execute(select(QuizJob).where(QuizJob.task_id == task_id)).scalar_one_or_none()
    if row is None:
        return
    row.status = status
    row.result_payload = result_payload
    row.updated_at = datetime.utcnow()
    db.add(row)
    db.flush()


def get_job_by_task_id(db: Session, task_id: str) -> Dict[str, Any] | None:
    lesson = db.execute(select(LessonJob).where(LessonJob.task_id == task_id)).scalar_one_or_none()
    if lesson:
        return {
            "kind": "lesson",
            "task_id": lesson.task_id,
            "job_id": lesson.job_id,
            "status": lesson.status.value,
            "topic": lesson.topic,
            "result": lesson.result_payload,
        }
    quiz = db.execute(select(QuizJob).where(QuizJob.task_id == task_id)).scalar_one_or_none()
    if quiz:
        return {
            "kind": "quiz",
            "task_id": quiz.task_id,
            "job_id": quiz.job_id,
            "status": quiz.status.value,
            "topic": quiz.topic,
            "result": quiz.result_payload,
        }
    return None


def create_or_get_quiz_attempt(
    db: Session,
    *,
    session_id: str,
    job_id: str,
    attempt_no: int,
    answers_payload: Dict[str, Any],
    graded_payload: Dict[str, Any],
) -> tuple[QuizAttempt, bool]:
    row = db.execute(
        select(QuizAttempt).where(
            QuizAttempt.session_id == session_id,
            QuizAttempt.job_id == job_id,
            QuizAttempt.attempt_no == attempt_no,
        )
    ).scalar_one_or_none()
    if row:
        return row, False

    score_percent = float((graded_payload.get("summary") or {}).get("score_percent", 0.0))
    row = QuizAttempt(
        session_id=session_id,
        job_id=job_id,
        attempt_no=attempt_no,
        score_percent=score_percent,
        answers_payload=answers_payload,
        graded_payload=graded_payload,
    )
    db.add(row)
    db.flush()
    return row, True


def add_mastery_snapshot(db: Session, *, session_id: str, mastery: Dict[str, float], difficulty_level: str) -> None:
    row = MasterySnapshot(
        session_id=session_id,
        avg_mastery=round(avg_mastery(mastery), 4),
        mastery_payload=mastery,
        difficulty_level=difficulty_level,
    )
    db.add(row)
    db.flush()


def add_transition(
    db: Session,
    *,
    session_id: str,
    action: str,
    avg: float,
    recommended_difficulty: str,
    weakest_skills: list[str],
    metadata_payload: Dict[str, Any] | None = None,
) -> None:
    row = AdaptiveTransition(
        session_id=session_id,
        action=action,
        avg_mastery=round(avg, 4),
        recommended_difficulty=recommended_difficulty,
        weakest_skills=weakest_skills,
        metadata_payload=metadata_payload or {},
    )
    db.add(row)
    db.flush()


def save_remediation(db: Session, *, session_id: str, payload: Dict[str, Any]) -> RemediationPack:
    row = db.execute(select(RemediationPack).where(RemediationPack.session_id == session_id)).scalar_one_or_none()
    if row is None:
        row = RemediationPack(session_id=session_id, payload=payload)
    else:
        row.payload = payload
        row.updated_at = datetime.utcnow()
    db.add(row)
    db.flush()
    return row


def get_remediation(db: Session, session_id: str) -> RemediationPack | None:
    return db.execute(select(RemediationPack).where(RemediationPack.session_id == session_id)).scalar_one_or_none()


def save_rag_collection(db: Session, *, collection_id: str, metadata_payload: Dict[str, Any]) -> None:
    row = db.get(RagCollection, collection_id)
    if row is None:
        row = RagCollection(id=collection_id, metadata_payload=metadata_payload)
    else:
        row.metadata_payload = metadata_payload
    db.add(row)
    db.flush()


def replace_rag_chunks(db: Session, *, collection_id: str, chunks: list[Dict[str, Any]]) -> None:
    db.query(RagChunk).filter(RagChunk.collection_id == collection_id).delete()
    for c in chunks:
        row = RagChunk(
            collection_id=collection_id,
            chunk_id=int(c.get("id") or 0),
            source=c.get("source") or "unknown",
            text=c.get("text") or "",
            weight_payload=c.get("weights") or {},
        )
        db.add(row)
    db.flush()
