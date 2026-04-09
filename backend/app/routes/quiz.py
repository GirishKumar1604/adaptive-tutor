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
    count_quiz_attempts,
    create_or_get_quiz_attempt,
    create_quiz_job,
    get_session_or_404,
    save_session_state,
    update_skill_bank_from_mastery,
    upsert_quiz_job_result,
)
from services.rag_service import query_collection_with_sources

router = APIRouter()

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/app/storage/videos")


def _next_action(avg: float) -> str:
    if avg < 0.45:
        return "REMEDIATE"
    if avg >= 0.80:
        return "ADVANCE"
    return "NEXT_QUIZ"


def _performance_suggestions(avg: float, weak: List[str], difficulty: str) -> List[str]:
    suggestions: List[str] = []
    if avg < 0.45:
        suggestions.append("Re-watch the lesson video to reinforce the fundamentals before retrying.")
        if weak:
            suggestions.append(f"Focus your review on: {', '.join(weak[:2])}.")
        suggestions.append("A remediation session is queued — work through it to boost your mastery.")
    elif avg < 0.80:
        suggestions.append("Good progress! A follow-up quiz will solidify your understanding.")
        if weak:
            suggestions.append(f"Give extra attention to: {', '.join(weak[:2])}.")
        if difficulty == "EASY":
            suggestions.append("You are ready to tackle medium-difficulty questions.")
        elif difficulty == "MEDIUM":
            suggestions.append("Keep pushing — hard questions are within reach.")
    else:
        suggestions.append("Outstanding mastery! You have fully grasped this topic.")
        suggestions.append("Challenge yourself by exploring an advanced topic or related concept.")
    return suggestions


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
    job_id: str = Field(..., min_length=6)
    topic: str = Field(..., min_length=2)
    preferred_language: Optional[str] = "English"
    answers: List[QuizAnswer]


class ReinforcementStartRequest(BaseModel):
    session_id: str
    job_id: str = Field(..., min_length=6)
    topic: str = Field(..., min_length=2)
    preferred_language: Optional[str] = "English"
    wrong_question_ids: List[str]
    num_questions: int = 3


class ReinforcementSubmitRequest(BaseModel):
    session_id: str
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

                attempt_no = count_quiz_attempts(db, session_id=req.session_id, job_id=req.job_id) + 1
                attempt, created = create_or_get_quiz_attempt(
                    db,
                    session_id=req.session_id,
                    job_id=req.job_id,
                    attempt_no=attempt_no,
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

                # Flush updated mastery back to the cross-session learner skill bank
                if session.learner_id:
                    update_skill_bank_from_mastery(
                        db,
                        learner_id=session.learner_id,
                        topic=session.topic,
                        mastery=state["mastery"],
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

                avg = round(avg_mastery(state["mastery"]), 4)
                weak = weakest_skills(state["mastery"])
                action = _next_action(avg)
                suggestions = _performance_suggestions(avg, weak, state["difficulty_level"])
                graded["adaptive"] = {
                    "session_id": req.session_id,
                    "student_state": {
                        "mastery": state["mastery"],
                        "difficulty_level": state["difficulty_level"],
                        "updated_at": datetime.utcnow().isoformat() + "Z",
                    },
                    "adaptive_summary": {
                        "avg_mastery": avg,
                        "difficulty_level": state["difficulty_level"],
                        "weakest_skills": weak,
                        "next_action": action,
                        "suggestions": suggestions,
                    },
                }
                if rag_sources:
                    graded["provenance"] = {"rag_sources": rag_sources}

            db.commit()

        return ok(result=graded, warnings=warnings)
    except Exception as e:
        return fail(error=f"Failed to grade quiz: {type(e).__name__}: {e}")


@router.post("/quiz/reinforcement/start")
def quiz_reinforcement_start(req: ReinforcementStartRequest):
    """Generate harder targeted questions for concepts missed in the main quiz."""
    try:
        from agents.quiz_agent import _generate_one_mcq, _display_skill, _segments_to_context

        job_dir = os.path.join(OUTPUT_DIR, req.job_id)
        quiz_full_path = os.path.join(job_dir, "quiz_full.json")
        if not os.path.exists(quiz_full_path):
            return fail(error="Original quiz not found. Complete the main quiz first.")

        quiz_full = _read_json(quiz_full_path)
        qmap = {q["id"]: q for q in (quiz_full.get("questions") or [])}
        # Also include latest reinforcement pool so repeated rounds can stay targeted.
        rein_full_path = os.path.join(job_dir, "reinforcement_full.json")
        if os.path.exists(rein_full_path):
            rein_full = _read_json(rein_full_path)
            for q in (rein_full.get("questions") or []):
                qid = q.get("id")
                if qid:
                    qmap[qid] = q

        # Collect unique skills from the wrong questions
        skill_list: List[str] = []
        seen_skills: set = set()
        for qid in req.wrong_question_ids:
            q = qmap.get(qid)
            if q:
                sk = q.get("skill") or f"{req.topic}::Core"
                if sk not in seen_skills:
                    skill_list.append(sk)
                    seen_skills.add(sk)

        if not skill_list:
            skill_list = [f"{req.topic}::Core"]

        # Load session state and bump difficulty one level higher
        with SessionLocal() as db:
            session = get_session_or_404(db, req.session_id)
            if not session:
                return fail(error="session_id not found")
            difficulty = session.difficulty_level or "EASY"
            student_state = session.student_state or {}

        harder_map = {"EASY": "MEDIUM", "MEDIUM": "HARD", "HARD": "HARD"}
        reinforcement_difficulty = harder_map.get(difficulty.upper(), "MEDIUM")

        # Load lesson segments for generation context
        segments_path = os.path.join(job_dir, "segments.json")
        ctx = "- No lesson context available."
        if os.path.exists(segments_path):
            segs_obj = _read_json(segments_path)
            segments = segs_obj.get("segments", []) or []
            ctx = _segments_to_context(segments) or ctx

        # Generate the requested number of reinforcement questions.
        # Do not cap by unique skill count; if all misses map to one skill
        # (common for Topic::Core), still produce a full follow-up set.
        num_to_gen = max(1, int(req.num_questions or 3))
        questions: List[Dict[str, Any]] = []
        wrong_focus_lines: List[str] = []
        for qid in req.wrong_question_ids:
            q = qmap.get(qid)
            if not q:
                continue
            prompt = " ".join((q.get("prompt") or "").split())[:160]
            exp = " ".join((q.get("explanation") or "").split())[:180]
            if prompt:
                wrong_focus_lines.append(f"- Missed concept: {prompt}")
            if exp:
                wrong_focus_lines.append(f"  Correct idea: {exp}")

        if wrong_focus_lines:
            ctx = (
                f"{ctx}\n\nPrevious weak areas to target:\n"
                + "\n".join(wrong_focus_lines[:10])
            ).strip()

        existing_prompt_texts = []
        for q in (quiz_full.get("questions") or []):
            p = " ".join((q.get("prompt") or "").split())
            if p:
                existing_prompt_texts.append(p)
        if os.path.exists(rein_full_path):
            rein_full = _read_json(rein_full_path)
            for q in (rein_full.get("questions") or []):
                p = " ".join((q.get("prompt") or "").split())
                if p:
                    existing_prompt_texts.append(p)

        for i in range(num_to_gen):
            skill_key = skill_list[i % len(skill_list)]
            skill_display = _display_skill(skill_key)
            avoid_line = (
                "Avoid repeating these exact prompts: "
                + " | ".join(existing_prompt_texts[:8])
            ) if existing_prompt_texts else ""
            q = _generate_one_mcq(
                q_index=i + 1,
                topic=req.topic,
                preferred_language=req.preferred_language or "English",
                user_goal=(
                    "Reinforce and deepen understanding of concepts missed in the quiz. "
                    f"Generate a fresh variant for round {i + 1}. {avoid_line}"
                ).strip(),
                student_state=student_state,
                context=ctx,
                difficulty_hint=reinforcement_difficulty,
                skill_focus_key=skill_key,
                skill_focus_display=skill_display,
            )
            q["id"] = f"R{i + 1}"
            questions.append(q)

        reinforcement_full = {
            "topic": req.topic,
            "difficulty": reinforcement_difficulty,
            "questions": questions,
        }

        # Save full version (with answers) for grading
        os.makedirs(job_dir, exist_ok=True)
        rein_full_path = os.path.join(job_dir, "reinforcement_full.json")
        with open(rein_full_path, "w", encoding="utf-8") as f:
            json.dump(reinforcement_full, f, ensure_ascii=False, indent=2)

        # Return public version (no correct_answer or explanation)
        public_qs = [
            {k: v for k, v in q.items() if k not in ("correct_answer", "explanation")}
            for q in questions
        ]
        return ok(result={
            "topic": req.topic,
            "difficulty": reinforcement_difficulty,
            "questions": public_qs,
        })
    except Exception as e:
        return fail(error=f"Failed to generate reinforcement: {type(e).__name__}: {e}")


@router.post("/quiz/reinforcement/submit")
def quiz_reinforcement_submit(req: ReinforcementSubmitRequest):
    """Grade the reinforcement round and update mastery."""
    try:
        from agents.quiz_agent import grade_quiz

        job_dir = os.path.join(OUTPUT_DIR, req.job_id)
        rein_full_path = os.path.join(job_dir, "reinforcement_full.json")
        if not os.path.exists(rein_full_path):
            return fail(error="Reinforcement quiz not found. Call /quiz/reinforcement/start first.")

        rein_full = _read_json(rein_full_path)
        graded = grade_quiz(
            topic=req.topic,
            student_state={},
            preferred_language=req.preferred_language or "English",
            quiz_full=rein_full,
            answers=[a.model_dump() for a in req.answers],
        )
        graded = _ensure_summary(graded)

        warnings: list[str] = []
        with SessionLocal() as db:
            session = get_session_or_404(db, req.session_id)
            if not session:
                return fail(error="session_id not found")

            state = {
                "topic": session.topic,
                "mastery": session.mastery or {},
                "difficulty_level": session.difficulty_level,
                "student_state": session.student_state or {},
            }
            state = update_mastery_from_quiz(
                state=state,
                quiz_full=rein_full,
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
                    "last_action": "REINFORCEMENT_SUBMIT",
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

            if session.learner_id:
                update_skill_bank_from_mastery(
                    db,
                    learner_id=session.learner_id,
                    topic=session.topic,
                    mastery=state["mastery"],
                )

            avg = round(avg_mastery(state["mastery"]), 4)
            weak = weakest_skills(state["mastery"])
            action = _next_action(avg)
            suggestions = _performance_suggestions(avg, weak, state["difficulty_level"])

            graded["adaptive"] = {
                "session_id": req.session_id,
                "student_state": {
                    "mastery": state["mastery"],
                    "difficulty_level": state["difficulty_level"],
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                },
                "adaptive_summary": {
                    "avg_mastery": avg,
                    "difficulty_level": state["difficulty_level"],
                    "weakest_skills": weak,
                    "next_action": action,
                    "suggestions": suggestions,
                },
            }
            db.commit()

        return ok(result=graded, warnings=warnings)
    except Exception as e:
        return fail(error=f"Failed to grade reinforcement: {type(e).__name__}: {e}")
