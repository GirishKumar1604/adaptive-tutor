import os
import uuid
import subprocess
from celery_app import celery
from config import OUTPUT_DIR


@celery.task(bind=True)
def render_manim_video(self, scene_name: str = "SampleScene") -> dict:
    """
    Renders a Manim scene to MP4 using Manim CLI.
    """
    job_id = str(uuid.uuid4())
    out_dir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(out_dir, exist_ok=True)

    scene_file = "manim_scenes/sample_scene.py"
    media_dir = out_dir  # manim will create media structure inside this

    cmd = [
        "manim",
        "-q", "l",                  # low quality (fast MVP). Switch to -q h later.
        "--media_dir", media_dir,
        scene_file,
        scene_name,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        return {
            "status": "FAILED",
            "job_id": job_id,
            "error": e.stderr[-2000:],  # last part for readability
        }

    # Manim outputs under: media_dir/videos/<module>/<quality>/<scene>.mp4
    # We'll locate the produced mp4.
    produced_mp4 = None
    for root, _, files in os.walk(media_dir):
        for f in files:
            if f.endswith(".mp4") and f.startswith(scene_name):
                produced_mp4 = os.path.join(root, f)
                break
        if produced_mp4:
            break

    if not produced_mp4:
        return {"status": "FAILED", "job_id": job_id, "error": "Rendered MP4 not found."}

    return {
        "status": "COMPLETED",
        "job_id": job_id,
        "video_path": produced_mp4,
    }
