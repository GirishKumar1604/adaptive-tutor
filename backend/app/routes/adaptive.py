from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from celery_app import celery
from db import SessionLocal
from services.api_response import fail, ok
from services.groq_client import chat_json
from services.mastery_service import (
    avg_mastery,
    recommend_difficulty,
    skill_key,
    update_mastery_from_remediation,
    weakest_skill_keys,
    weakest_skills,
)
from services.persistence_service import (
    add_mastery_snapshot,
    add_transition,
    create_lesson_job,
    create_quiz_job,
    create_session as persist_session,
    flush_skill_bank_to_mastery,
    get_or_create_learner,
    get_remediation,
    get_session_or_404,
    get_skill_bank,
    save_remediation,
    save_session_state,
    update_skill_bank_from_mastery,
)

router = APIRouter()
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/app/storage/videos")


REMEDIATION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "focus_skills": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
        "difficulty": {"type": "string", "enum": ["EASY", "MEDIUM", "HARD"]},
        "bullets": {"type": "array", "items": {"type": "string"}, "minItems": 6, "maxItems": 6},
        "tiny_example": {"type": "string"},
        "checks": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "prompt": {"type": "string"},
                    "options": {
                        "type": "array",
                        "minItems": 4,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "enum": ["A", "B", "C", "D"]},
                                "text": {"type": "string"},
                            },
                            "required": ["id", "text"],
                            "additionalProperties": False,
                        },
                    },
                    "correct_answer": {"type": "string", "enum": ["A", "B", "C", "D"]},
                    "explanation": {"type": "string"},
                },
                "required": ["id", "prompt", "options", "correct_answer", "explanation"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["topic", "focus_skills", "difficulty", "bullets", "tiny_example", "checks"],
    "additionalProperties": False,
}


class AdaptiveStartRequest(BaseModel):
    topic: str = Field(..., min_length=2)
    preferred_language: Optional[str] = "English"
    rag_collection_id: Optional[str] = None
    quality: str = "low"
    student_state: Optional[Dict[str, Any]] = None
    learner_id: Optional[str] = None


class AdaptiveStepRequest(BaseModel):
    session_id: str = Field(..., min_length=6)
    num_questions: int = 8
    force_remediate: bool = False


class AdaptiveNextQuizRequest(BaseModel):
    session_id: str = Field(..., min_length=6)
    num_questions: int = 8


class RemediateRequest(BaseModel):
    session_id: str = Field(..., min_length=6)


class RemediationAnswer(BaseModel):
    question_id: str
    answer: str


class RemediationSubmitRequest(BaseModel):
    session_id: str
    answers: List[RemediationAnswer]


class NextActionRequest(BaseModel):
    session_id: str


def _build_remediation(*, topic: str, lang: str, difficulty: str, focus_skills: List[str]) -> Dict[str, Any]:
    system = f"You are an expert tutor. Output VALID JSON only. Language: {lang}."
    user = f"""
Topic: {topic}
Focus skills: {focus_skills}
Difficulty: {difficulty}
Return JSON:
- topic
- focus_skills
- difficulty
- bullets (exactly 6)
- tiny_example
- checks (exactly 3 MCQs with A/B/C/D options and explanation)
"""
    return chat_json(
        system=system,
        user=user,
        schema_name="remediation_pack",
        schema=REMEDIATION_SCHEMA,
        strict=True,
        temperature=0.2,
        max_tokens=2800,
    )


def _fallback_remediation(*, topic: str, difficulty: str, focus_skills: List[str]) -> Dict[str, Any]:
    focus = focus_skills[:3] if focus_skills else ["Core"]
    return {
        "topic": topic,
        "focus_skills": focus,
        "difficulty": difficulty if difficulty in {"EASY", "MEDIUM", "HARD"} else "EASY",
        "bullets": [
            f"Restate {topic} in one sentence before solving.",
            "Identify known inputs and the expected output clearly.",
            "Apply the core rule step by step; avoid skipping transitions.",
            "Verify intermediate values against constraints.",
            "Check one concrete example end-to-end.",
            "Summarize the final takeaway in plain language.",
        ],
        "tiny_example": f"Mini example for {topic}: list inputs, apply one step, and verify the output.",
        "checks": [
            {
                "id": "R1",
                "prompt": "What is the most reliable first step before solving?",
                "options": [
                    {"id": "A", "text": "Guess the final answer directly"},
                    {"id": "B", "text": "Restate the problem and identify inputs/outputs"},
                    {"id": "C", "text": "Ignore constraints to move faster"},
                    {"id": "D", "text": "Pick the longest option"},
                ],
                "correct_answer": "B",
                "explanation": "Clarifying the problem and variables prevents avoidable mistakes.",
            },
            {
                "id": "R2",
                "prompt": "During problem solving, what should be done after each step?",
                "options": [
                    {"id": "A", "text": "Skip checks to save time"},
                    {"id": "B", "text": "Change method randomly"},
                    {"id": "C", "text": "Verify intermediate values against constraints"},
                    {"id": "D", "text": "Always restart from scratch"},
                ],
                "correct_answer": "C",
                "explanation": "Intermediate verification catches drift early.",
            },
            {
                "id": "R3",
                "prompt": "Which action best consolidates learning after solving?",
                "options": [
                    {"id": "A", "text": "Memorize the final number only"},
                    {"id": "B", "text": "Skip recap and move on"},
                    {"id": "C", "text": "Try unrelated shortcuts"},
                    {"id": "D", "text": "Summarize the key takeaway in plain language"},
                ],
                "correct_answer": "D",
                "explanation": "A short recap improves transfer and retention.",
            },
        ],
    }


def _decide_next_action(avg: float, force_remediate: bool = False) -> Literal["REMEDIATE", "NEXT_QUIZ", "ADVANCE"]:
    if force_remediate or avg < 0.45:
        return "REMEDIATE"
    if avg >= 0.80:
        return "ADVANCE"
    return "NEXT_QUIZ"


@router.post("/adaptive/start")
def adaptive_start(req: AdaptiveStartRequest):
    session_id = os.urandom(16).hex()
    job_id = os.urandom(16).hex()

    with SessionLocal() as db:
        # Seed mastery from the learner's skill bank if a learner_id is provided
        if req.learner_id:
            get_or_create_learner(db, req.learner_id)
            bank = get_skill_bank(db, req.learner_id)
            mastery = flush_skill_bank_to_mastery(db, learner_id=req.learner_id, topic=req.topic, skill_bank=bank)
        else:
            mastery = {skill_key(req.topic, "Core"): 0.5}

        from services.mastery_service import avg_mastery as _avg, recommend_difficulty
        seeded_avg = _avg(mastery)
        difficulty_level = recommend_difficulty(seeded_avg)

        task = celery.send_task(
            "tasks.learn_pipeline.generate_lesson_video",
            kwargs={
                "job_id": job_id,
                "topic": req.topic,
                "preferred_language": req.preferred_language or "English",
                "quality": req.quality or "low",
                "student_state": req.student_state or {},
                "rag_collection_id": req.rag_collection_id,
            },
        )

        session = persist_session(
            db,
            session_id=session_id,
            job_id=job_id,
            topic=req.topic,
            preferred_language=req.preferred_language or "English",
            rag_collection_id=req.rag_collection_id,
            mastery=mastery,
            student_state=req.student_state or {},
            difficulty_level=difficulty_level,
        )
        session.learner_id = req.learner_id
        create_lesson_job(db, job_id=job_id, task_id=task.id, topic=req.topic, session_id=session_id)
        add_mastery_snapshot(db, session_id=session_id, mastery=mastery, difficulty_level=difficulty_level)
        db.commit()

    return ok(result={"session_id": session_id, "job_id": job_id, "task_id": task.id}, state="PENDING")


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    with SessionLocal() as db:
        session = get_session_or_404(db, session_id)
        if not session:
            return fail(error="session_id not found")
        return ok(
            result={
                "session_id": session.id,
                "topic": session.topic,
                "preferred_language": session.preferred_language,
                "rag_collection_id": session.rag_collection_id,
                "job_id": session.job_id,
                "mastery": session.mastery,
                "difficulty_level": session.difficulty_level,
                "last_action": session.last_action,
                "updated_at": session.updated_at.isoformat() + "Z",
            }
        )


@router.get("/adaptive/state/{session_id}")
def adaptive_state(session_id: str):
    return get_session(session_id)


@router.post("/adaptive/next-action")
def adaptive_next_action(req: NextActionRequest):
    with SessionLocal() as db:
        session = get_session_or_404(db, req.session_id)
        if not session:
            return fail(error="session_id not found")
        mastery = session.mastery or {}
        avg = avg_mastery(mastery)
        rec_diff = recommend_difficulty(avg)
        action = _decide_next_action(avg)
        weak = weakest_skills(mastery)

        save_session_state(
            db,
            session,
            {
                "mastery": mastery,
                "student_state": session.student_state or {},
                "difficulty_level": rec_diff,
                "last_action": action,
                "last_quiz_task_id": session.last_quiz_task_id,
                "last_remediation_path": session.last_remediation_path,
            },
        )
        add_transition(
            db,
            session_id=session.id,
            action=action,
            avg=avg,
            recommended_difficulty=rec_diff,
            weakest_skills=weak,
        )
        db.commit()
        return ok(
            result={
                "session_id": session.id,
                "action": action,
                "recommended_difficulty": rec_diff,
                "avg_mastery": round(avg, 4),
                "weakest_skills": weak,
            }
        )


@router.post("/adaptive/next-quiz")
def adaptive_next_quiz(req: AdaptiveNextQuizRequest):
    with SessionLocal() as db:
        session = get_session_or_404(db, req.session_id)
        if not session:
            return fail(error="session_id not found")
        mastery = session.mastery or {}
        weak_keys = weakest_skill_keys(mastery) or [skill_key(session.topic, "Core")]
        student_state = {
            **(session.student_state or {}),
            "session_id": session.id,
            "difficulty_level": session.difficulty_level,
            "mastery": mastery,
            "weakest_skills": weak_keys,
        }
        task = celery.send_task(
            "tasks.quiz_pipeline.generate_quiz",
            kwargs={
                "job_id": session.job_id,
                "topic": session.topic,
                "preferred_language": session.preferred_language,
                "student_state": student_state,
                "num_questions": req.num_questions,
                "rag_collection_id": session.rag_collection_id,
            },
        )
        create_quiz_job(db, job_id=session.job_id, task_id=task.id, topic=session.topic, session_id=session.id)
        save_session_state(
            db,
            session,
            {
                "mastery": mastery,
                "student_state": session.student_state or {},
                "difficulty_level": session.difficulty_level,
                "last_action": "NEXT_QUIZ",
                "last_quiz_task_id": task.id,
                "last_remediation_path": session.last_remediation_path,
            },
        )
        db.commit()
        return ok(
            state="PENDING",
            result={
                "session_id": session.id,
                "job_id": session.job_id,
                "topic": session.topic,
                "task_id": task.id,
                "student_state_used": student_state,
            },
        )


@router.get("/adaptive/quiz/{session_id}")
def adaptive_quiz(session_id: str):
    with SessionLocal() as db:
        session = get_session_or_404(db, session_id)
        if not session:
            return fail(error="session_id not found")
        quiz_path = os.path.join(OUTPUT_DIR, session.job_id or "", "quiz.json")
    if not os.path.exists(quiz_path):
        return fail(error="quiz.json not ready. Call /adaptive/next-quiz first.")
    with open(quiz_path, "r", encoding="utf-8") as f:
        return ok(result=json.load(f))


@router.post("/adaptive/remediate")
def adaptive_remediate(req: RemediateRequest):
    with SessionLocal() as db:
        session = get_session_or_404(db, req.session_id)
        if not session:
            return fail(error="session_id not found")
        mastery = session.mastery or {}
        focus = weakest_skills(mastery) or ["Core"]
        try:
            data = _build_remediation(
                topic=session.topic,
                lang=session.preferred_language,
                difficulty=session.difficulty_level,
                focus_skills=focus,
            )
        except Exception:
            data = _fallback_remediation(
                topic=session.topic,
                difficulty=session.difficulty_level,
                focus_skills=focus,
            )
        payload = {
            **data,
            "session_id": session.id,
            "job_id": session.job_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }
        save_remediation(db, session_id=session.id, payload=payload)
        save_session_state(
            db,
            session,
            {
                "mastery": mastery,
                "student_state": session.student_state or {},
                "difficulty_level": session.difficulty_level,
                "last_action": "REMEDIATE",
                "last_quiz_task_id": session.last_quiz_task_id,
                "last_remediation_path": f"db://remediation/{session.id}",
            },
        )
        db.commit()
        return ok(result={"session_id": session.id, "remediation": payload})


@router.get("/adaptive/remediation/{session_id}")
def adaptive_get_remediation(session_id: str):
    with SessionLocal() as db:
        session = get_session_or_404(db, session_id)
        if not session:
            return fail(error="session_id not found")
        row = get_remediation(db, session_id)
        if not row:
            return fail(error="remediation not found. Call /adaptive/remediate first.")
        return ok(result=row.payload)


@router.post("/adaptive/remediation/submit")
def adaptive_remediation_submit(req: RemediationSubmitRequest):
    with SessionLocal() as db:
        session = get_session_or_404(db, req.session_id)
        if not session:
            return fail(error="session_id not found")
        row = get_remediation(db, req.session_id)
        if not row:
            return fail(error="remediation not found. Call /adaptive/remediate first.")

        checks = (row.payload or {}).get("checks") or []
        cmap = {c.get("id"): c for c in checks}
        results = []
        correct = 0

        for a in req.answers:
            q = cmap.get(a.question_id)
            if not q:
                results.append({"question_id": a.question_id, "correct": False, "score": 0.0, "feedback": "Question not found."})
                continue
            ca = (q.get("correct_answer") or "").strip().upper()
            ua = (a.answer or "").strip().upper()
            if ua == ca:
                correct += 1
                results.append({"question_id": a.question_id, "correct": True, "score": 1.0, "feedback": "Correct."})
            else:
                exp = q.get("explanation") or ""
                results.append({"question_id": a.question_id, "correct": False, "score": 0.0, "feedback": f"Incorrect. Correct is {ca}. {exp}"})

        total = len(results) or 1
        score = correct / total
        state = update_mastery_from_remediation(
            state={
                "topic": session.topic,
                "mastery": session.mastery or {},
                "difficulty_level": session.difficulty_level,
                "student_state": session.student_state or {},
            },
            score=score,
        )
        next_action = _decide_next_action(avg_mastery(state["mastery"]))
        save_session_state(
            db,
            session,
            {
                "mastery": state["mastery"],
                "student_state": state["student_state"],
                "difficulty_level": state["difficulty_level"],
                # Mark the remediation cycle as completed so the next adaptive step
                # can move back into a quiz instead of chaining remediation forever.
                "last_action": "REMEDIATION_SUBMIT",
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
        add_transition(
            db,
            session_id=session.id,
            action="REMEDIATION_SUBMIT",
            avg=avg_mastery(state["mastery"]),
            recommended_difficulty=state["difficulty_level"],
            weakest_skills=weakest_skills(state["mastery"]),
        )
        db.commit()
        return ok(
            result={
                "session_id": session.id,
                "score_percent": round(score * 100, 2),
                "correct": correct,
                "total": len(results),
                "results": results,
                "updated_state": {
                    "mastery": state["mastery"],
                    "difficulty_level": state["difficulty_level"],
                    "next_action": next_action,
                },
            }
        )


@router.post("/adaptive/step")
def adaptive_step(req: AdaptiveStepRequest):
    with SessionLocal() as db:
        session = get_session_or_404(db, req.session_id)
        if not session:
            return fail(error="session_id not found")

        # Always let a fresh session see at least one quiz before remediation.
        if not req.force_remediate and not session.last_quiz_task_id:
            action = "NEXT_QUIZ"
        # After a remediation submission, route back to a quiz to assess progress.
        elif not req.force_remediate and session.last_action == "REMEDIATION_SUBMIT":
            action = "NEXT_QUIZ"
        # If we're already in a quiz progression flow, keep retrying quiz generation.
        elif not req.force_remediate and session.last_action == "NEXT_QUIZ":
            action = "NEXT_QUIZ"
        else:
            action = _decide_next_action(avg_mastery(session.mastery or {}), req.force_remediate)
    if action == "REMEDIATE":
        return adaptive_remediate(RemediateRequest(session_id=req.session_id))
    return adaptive_next_quiz(AdaptiveNextQuizRequest(session_id=req.session_id, num_questions=req.num_questions))
