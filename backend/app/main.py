import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from routes.diagnose import router as diagnose_router
from routes.learn import router as learn_router
from routes.quiz import router as quiz_router
from routes.rag import router as rag_router
from routes.adaptive import router as adaptive_router
from routes.coach import router as coach_router

from tasks.render import render_manim_video
from celery_app import celery
from config import REDIS_URL
from db import check_db_ready, init_db, SessionLocal
from services.api_response import fail, ok
from services.persistence_service import get_job_by_task_id

app = FastAPI(title="Adaptive Tutor MVP", version="0.1.0")

app.include_router(diagnose_router, tags=["Diagnose"])
app.include_router(learn_router, tags=["Learn"])
app.include_router(quiz_router, tags=["Quiz"])
app.include_router(rag_router, tags=["RAG"])
app.include_router(adaptive_router, tags=["Adaptive"])
app.include_router(coach_router, tags=["Coach"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health", tags=["Health"])
def health():
    return ok(result={"status": "ok"})


@app.get("/health/ready", tags=["Health"])
def health_ready():
    db_ok = check_db_ready()
    redis_ok = False
    worker_ok = False

    try:
        import redis

        client = redis.from_url(REDIS_URL)
        redis_ok = bool(client.ping())
    except Exception:
        redis_ok = False

    try:
        stats = celery.control.inspect().stats() or {}
        worker_ok = bool(stats)
    except Exception:
        worker_ok = False

    ready = db_ok and redis_ok and worker_ok
    state = "SUCCESS" if ready else "FAILURE"
    return ok(
        state=state,
        result={
            "ready": ready,
            "checks": {"database": db_ok, "redis": redis_ok, "worker": worker_ok},
        },
    )


@app.get("/jobs/{task_id}", tags=["Jobs"])
def get_job(task_id: str):
    with SessionLocal() as db:
        row = get_job_by_task_id(db, task_id)
    if not row:
        return fail(error="job not found")
    return ok(result=row, state=row["status"])


@app.post("/video/sample", tags=["Video"])
def create_sample_video():
    task = render_manim_video.delay("SampleScene")
    return ok(result={"task_id": task.id}, state="PENDING")


@app.get("/video/status/{task_id}", tags=["Video"])
def get_status(task_id: str):
    res = render_manim_video.AsyncResult(task_id)
    if res.state in ("PENDING", "STARTED", "RETRY"):
        return ok(state=res.state, result=None)
    if res.state == "FAILURE":
        return fail(state="FAILURE", error=str(res.info))
    return ok(state="SUCCESS", result=res.result)


@app.get("/video/download/{task_id}", tags=["Video"])
def download_video(task_id: str):
    res = render_manim_video.AsyncResult(task_id)
    if res.state != "SUCCESS":
        raise HTTPException(status_code=409, detail={"error": "Video not ready", "state": res.state})
    video_path = (res.result or {}).get("video_path")
    if not video_path:
        raise HTTPException(status_code=500, detail="Task succeeded but video_path missing")
    return FileResponse(
        video_path,
        media_type="video/mp4",
        filename="output.mp4",
        headers={"Accept-Ranges": "bytes"},
    )
