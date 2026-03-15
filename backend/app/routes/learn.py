import os
from typing import Optional, Literal, Any, Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from celery_app import celery

router = APIRouter()


class LearnStartRequest(BaseModel):
    topic: str = Field(..., min_length=2)
    user_goal: Optional[str] = None
    preferred_language: Optional[str] = "English"
    quality: Literal["low", "medium"] = "low"

    # Optional: if user uploaded documents for RAG, they pass the collection id here.
    collection_id: Optional[str] = None


class LearnStartResponse(BaseModel):
    job_id: str
    task_id: str


@router.post("/learn/start", response_model=LearnStartResponse)
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
                "student_state": {},  # optional / future use
                "rag_collection_id": req.collection_id,  # optional
            },
        )
        return LearnStartResponse(job_id=job_id, task_id=async_result.id)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to start learn pipeline: {type(e).__name__}: {e}",
        )


@router.get("/learn/status/{task_id}")
def learn_status(task_id: str):
    res = celery.AsyncResult(task_id)

    if res.state in ("PENDING", "STARTED", "RETRY"):
        return {"state": res.state}

    if res.state == "FAILURE":
        return {"state": "FAILURE", "error": str(res.info)}

    return {"state": "SUCCESS", "result": res.result}


@router.get("/learn/download/{task_id}")
def learn_download(task_id: str):
    res = celery.AsyncResult(task_id)

    if res.state != "SUCCESS":
        # 409 = not ready / conflict with current state
        raise HTTPException(status_code=409, detail={"error": "Video not ready", "state": res.state})

    result: Dict[str, Any] = res.result or {}

    # Prefer final video (with TTS) if present; otherwise fallback to silent video.
    video_path = result.get("final_video_path") or result.get("video_path")

    if not video_path or not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail="Video file not found")

    return FileResponse(video_path, media_type="video/mp4", filename="lesson.mp4")
