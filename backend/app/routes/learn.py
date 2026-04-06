import os
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from celery_app import celery
from db import SessionLocal
from models import JobStatus
from services.api_response import fail, ok
from services.persistence_service import create_lesson_job, upsert_lesson_job_result

router = APIRouter()


class LearnStartRequest(BaseModel):
    topic: str = Field(..., min_length=2)
    user_goal: Optional[str] = None
    preferred_language: Optional[str] = "English"
    quality: Literal["low", "medium"] = "low"
    collection_id: Optional[str] = None


class LearnStartResponse(BaseModel):
    job_id: str
    task_id: str


@router.post("/learn/start")
def learn_start(req: LearnStartRequest):
    job_id = os.urandom(16).hex()
    try:
        async_result = celery.send_task(
            "tasks.learn_pipeline.generate_lesson_video",
            kwargs={
                "job_id": job_id,
                "topic": req.topic,
                "user_goal": req.user_goal,
                "preferred_language": req.preferred_language or "English",
                "quality": req.quality,
                "student_state": {},
                "rag_collection_id": req.collection_id,
            },
        )
        with SessionLocal() as db:
            create_lesson_job(db, job_id=job_id, task_id=async_result.id, topic=req.topic)
            db.commit()
        return ok(result=LearnStartResponse(job_id=job_id, task_id=async_result.id).model_dump(), state="PENDING")
    except Exception as e:
        return fail(error=f"Failed to start learn pipeline: {type(e).__name__}: {e}")


@router.get("/learn/status/{task_id}")
def learn_status(task_id: str):
    res = celery.AsyncResult(task_id)
    with SessionLocal() as db:
        if res.state in ("PENDING", "STARTED", "RETRY"):
            upsert_lesson_job_result(
                db,
                task_id=task_id,
                status=JobStatus.RUNNING if res.state == "STARTED" else JobStatus.PENDING,
                result_payload={},
                warning=None,
            )
            db.commit()
            return ok(state=res.state, result=None)

        if res.state == "FAILURE":
            upsert_lesson_job_result(
                db,
                task_id=task_id,
                status=JobStatus.FAILURE,
                result_payload={"error": str(res.info)},
                warning=str(res.info),
            )
            db.commit()
            return fail(state="FAILURE", error=str(res.info))

        payload = dict(res.result or {})
        payload["served_video_path"] = payload.get("canonical_video_path") or payload.get("final_video_path") or payload.get("video_path")
        status = JobStatus.PARTIAL if payload.get("tts_error") else JobStatus.SUCCESS
        upsert_lesson_job_result(db, task_id=task_id, status=status, result_payload=payload, warning=payload.get("tts_error"))
        db.commit()
        warnings = [payload.get("tts_error")] if payload.get("tts_error") else []
        return ok(state=status.value, result=payload, warnings=warnings)


@router.get("/learn/download/{task_id}")
def learn_download(task_id: str):
    res = celery.AsyncResult(task_id)
    if res.state != "SUCCESS":
        raise HTTPException(status_code=409, detail={"error": "Video not ready", "state": res.state})

    result: Dict[str, Any] = res.result or {}
    video_path = result.get("canonical_video_path") or result.get("final_video_path") or result.get("video_path")
    if not video_path or not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(
        video_path,
        media_type="video/mp4",
        filename="lesson.mp4",
        headers={"Accept-Ranges": "bytes"},
    )
