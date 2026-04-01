import uuid

from fastapi import APIRouter

from agents.diagnoser_agent import evaluate_and_build_state, generate_questions
from agents.story_agent import generate_story_explanation
from celery_app import celery
from db import SessionLocal
from schemas.diagnose import (
    StartSessionRequest,
    StartSessionResponse,
    SubmitDiagnoseRequest,
    SubmitDiagnoseResponse,
)
from services.api_response import fail, ok
from services.mastery_service import skill_key
from services.persistence_service import (
    add_mastery_snapshot,
    create_lesson_job,
    create_session as persist_session,
    get_session_or_404,
    save_session_state,
)

router = APIRouter()


@router.post("/session/start")
def start_session(req: StartSessionRequest):
    session_id = str(uuid.uuid4())

    try:
        questions = generate_questions(req.topic)
    except Exception as e:
        return fail(error=f"LLM failed to generate questions: {type(e).__name__}: {e}")

    mastery = {skill_key(req.topic, "Core"): 0.5}
    with SessionLocal() as db:
        persist_session(
            db,
            session_id=session_id,
            job_id="",
            topic=req.topic,
            preferred_language=req.preferred_language or "English",
            rag_collection_id=None,
            mastery=mastery,
            student_state={
                "diagnose": {
                    "user_goal": req.user_goal,
                    "questions": [q.model_dump() for q in questions],
                }
            },
            difficulty_level="EASY",
        )
        add_mastery_snapshot(db, session_id=session_id, mastery=mastery, difficulty_level="EASY")
        db.commit()

    return ok(result=StartSessionResponse(session_id=session_id, questions=questions).model_dump())


@router.post("/diagnose/submit")
def submit_diagnose(req: SubmitDiagnoseRequest):
    with SessionLocal() as db:
        session = get_session_or_404(db, req.session_id)
        if not session:
            return fail(error="Session not found")

        diagnose_data = (session.student_state or {}).get("diagnose") or {}
        questions = diagnose_data.get("questions") or []
        if not questions:
            return fail(error="Diagnose questions not found for session")

        topic = session.topic
        valid_ids = {q.get("id") for q in questions}
        bad_ids = [a.question_id for a in req.answers if a.question_id not in valid_ids]
        if bad_ids:
            return fail(error=f"Unknown question_id(s): {bad_ids}")

        req2 = req.model_copy(update={"topic": topic})
        try:
            state = evaluate_and_build_state(req2)
        except Exception as e:
            return fail(error=f"LLM failed to evaluate state: {type(e).__name__}: {e}")

        try:
            story = generate_story_explanation(
                topic=topic,
                student_state=state,
                user_goal=diagnose_data.get("user_goal"),
                preferred_language=session.preferred_language or "English",
            )
        except Exception as e:
            return fail(error=f"LLM failed to generate story: {type(e).__name__}: {e}")

        learn_job_id = str(uuid.uuid4())
        try:
            async_result = celery.send_task(
                "tasks.learn_pipeline.generate_lesson_video",
                kwargs={
                    "job_id": learn_job_id,
                    "topic": topic,
                    "user_goal": diagnose_data.get("user_goal"),
                    "preferred_language": session.preferred_language or "English",
                    "quality": "low",
                    "student_state": state.model_dump(),
                },
            )
            learn_task_id = async_result.id
        except Exception as e:
            return fail(error=f"Failed to start learn pipeline: {type(e).__name__}: {e}")

        session.job_id = learn_job_id
        save_session_state(
            db,
            session,
            {
                "mastery": session.mastery or {},
                "difficulty_level": session.difficulty_level,
                "student_state": {
                    **(session.student_state or {}),
                    "diagnose_result": state.model_dump(),
                },
                "last_action": "DIAGNOSE_SUBMIT",
                "last_quiz_task_id": session.last_quiz_task_id,
                "last_remediation_path": session.last_remediation_path,
            },
        )
        create_lesson_job(db, job_id=learn_job_id, task_id=learn_task_id, topic=topic, session_id=req.session_id)
        add_mastery_snapshot(db, session_id=req.session_id, mastery=session.mastery or {}, difficulty_level=session.difficulty_level)
        db.commit()

    return ok(
        result=SubmitDiagnoseResponse(
            student_state=state,
            story_explanation=story,
            learn_job_id=learn_job_id,
            learn_task_id=learn_task_id,
        ).model_dump(),
        state="PENDING",
    )
