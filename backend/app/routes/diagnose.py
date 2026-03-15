# backend/app/routes/diagnose.py
import uuid
from fastapi import APIRouter, HTTPException

from schemas.diagnose import (
    StartSessionRequest,
    StartSessionResponse,
    SubmitDiagnoseRequest,
    SubmitDiagnoseResponse,
)

from agents.diagnoser_agent import generate_questions, evaluate_and_build_state
from agents.story_agent import generate_story_explanation

from celery_app import celery  # ✅ don't import tasks.learn_pipeline here

router = APIRouter()
SESSIONS = {}


@router.post("/session/start", response_model=StartSessionResponse)
def start_session(req: StartSessionRequest):
    session_id = str(uuid.uuid4())

    try:
        questions = generate_questions(req.topic)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"LLM failed to generate questions: {type(e).__name__}: {e}",
        )

    SESSIONS[session_id] = {
        "topic": req.topic,
        "user_goal": req.user_goal,
        "preferred_language": req.preferred_language or "English",
        "questions": [q.model_dump() for q in questions],
    }

    return StartSessionResponse(session_id=session_id, questions=questions)


@router.post("/diagnose/submit", response_model=SubmitDiagnoseResponse)
def submit_diagnose(req: SubmitDiagnoseRequest):
    session = SESSIONS.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    topic = session["topic"]

    # ✅ validate question ids
    valid_ids = {q["id"] for q in session["questions"]}
    bad_ids = [a.question_id for a in req.answers if a.question_id not in valid_ids]
    if bad_ids:
        raise HTTPException(status_code=422, detail=f"Unknown question_id(s): {bad_ids}")

    # ✅ force topic from session
    req2 = req.model_copy(update={"topic": topic})

    try:
        state = evaluate_and_build_state(req2)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"LLM failed to evaluate state: {type(e).__name__}: {e}",
        )

    try:
        story = generate_story_explanation(
            topic=topic,
            student_state=state,
            user_goal=session.get("user_goal"),
            preferred_language=session.get("preferred_language") or "English",
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"LLM failed to generate story: {type(e).__name__}: {e}",
        )

    session["student_state"] = state.model_dump()

    # ✅ auto-start learn pipeline (NO IMPORT of tasks.learn_pipeline)
    learn_job_id = str(uuid.uuid4())
    try:
        async_result = celery.send_task(
            "tasks.learn_pipeline.generate_lesson_video",
            kwargs={
                "job_id": learn_job_id,
                "topic": topic,
                "user_goal": session.get("user_goal"),
                "preferred_language": session.get("preferred_language") or "English",
                "quality": "low",
                "student_state": state.model_dump(),
            },
        )
        learn_task_id = async_result.id
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to start learn pipeline: {type(e).__name__}: {e}",
        )

    return SubmitDiagnoseResponse(
        student_state=state,
        story_explanation=story,
        learn_job_id=learn_job_id,
        learn_task_id=learn_task_id,
    )
