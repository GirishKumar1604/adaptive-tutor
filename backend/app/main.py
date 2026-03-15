from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from routes.diagnose import router as diagnose_router
from routes.learn import router as learn_router
from routes.quiz import router as quiz_router
from routes.rag import router as rag_router
from routes.adaptive import router as adaptive_router

from tasks.render import render_manim_video

app = FastAPI(title="Adaptive Tutor MVP", version="0.1.0")

app.include_router(diagnose_router, tags=["Diagnose"])
app.include_router(learn_router, tags=["Learn"])
app.include_router(quiz_router, tags=["Quiz"])
app.include_router(rag_router, tags=["RAG"])
app.include_router(adaptive_router, tags=["Adaptive"])



@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok"}

@app.post("/video/sample", tags=["Video"])
def create_sample_video():
    task = render_manim_video.delay("SampleScene")
    return {"task_id": task.id}

@app.get("/video/status/{task_id}", tags=["Video"])
def get_status(task_id: str):
    res = render_manim_video.AsyncResult(task_id)
    if res.state in ("PENDING", "STARTED", "RETRY"):
        return {"state": res.state}
    if res.state == "FAILURE":
        return {"state": "FAILURE", "error": str(res.info)}
    return {"state": "SUCCESS", "result": res.result}

@app.get("/video/download/{task_id}", tags=["Video"])
def download_video(task_id: str):
    res = render_manim_video.AsyncResult(task_id)
    if res.state != "SUCCESS":
        raise HTTPException(status_code=409, detail={"error": "Video not ready", "state": res.state})
    video_path = (res.result or {}).get("video_path")
    if not video_path:
        raise HTTPException(status_code=500, detail="Task succeeded but video_path missing")
    return FileResponse(video_path, media_type="video/mp4", filename="output.mp4")
