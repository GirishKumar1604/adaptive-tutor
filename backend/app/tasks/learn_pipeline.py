from __future__ import annotations

import os
import json
import glob
import subprocess
from datetime import datetime
from typing import Any, Dict, Optional

from celery_app import celery
from services.groq_client import chat_json

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/app/storage/videos")
GENERATED_DIR = "/app/app/manim_scenes/generated"

SEGMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "minItems": 6,
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "minimum": 1},
                    "title": {"type": "string"},
                    "narration": {"type": "string"},
                    "on_screen": {"type": "string"},
                    "duration_sec": {"type": "number", "minimum": 2, "maximum": 12},
                },
                "required": ["id", "title", "narration", "on_screen", "duration_sec"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["segments"],
    "additionalProperties": False,
}


def _py_str(s: str) -> str:
    """
    Escape for python string literal inside generated manim file.
    IMPORTANT: replace real newlines with \\n so generated .py is valid.
    """
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\r", "")
    s = s.replace("\n", "\\n")
    return f"\"{s}\""


def _write_scene_file(job_id: str, scene_class: str, topic: str, segments: list[dict]) -> str:
    os.makedirs(GENERATED_DIR, exist_ok=True)
    scene_file = os.path.join(GENERATED_DIR, f"lesson_{job_id.replace('-', '_')}.py")

    lines: list[str] = []
    lines.append("from manim import *")
    lines.append("")
    lines.append(f"class {scene_class}(Scene):")
    lines.append("    def construct(self):")
    lines.append("        self.camera.background_color = BLACK")
    lines.append(f"        title = Text({_py_str(topic)}, font_size=52).to_edge(UP)")
    lines.append("        self.play(FadeIn(title), run_time=0.6)")
    lines.append("        self.wait(0.2)")
    lines.append("        self.play(FadeOut(title), run_time=0.4)")
    lines.append("")

    for seg in segments:
        seg_title = seg.get("title", f"Segment {seg.get('id')}")
        on_screen = seg.get("on_screen", "")
        duration = float(seg.get("duration_sec", 6.0))

        lines.append(f"        seg_title = Text({_py_str(seg_title)}, font_size=44).to_edge(UP)")
        lines.append(f"        body = Text({_py_str(on_screen)}, font_size=30, line_spacing=1.1).scale(0.9)")
        lines.append("        body.move_to(ORIGIN)")
        lines.append("        self.play(FadeIn(seg_title), Write(body), run_time=0.8)")
        lines.append(f"        self.wait({max(1.0, min(duration, 12.0))})")
        lines.append("        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)")
        lines.append("")

    lines.append("        end = Text(\"End\", font_size=46).move_to(ORIGIN)")
    lines.append("        self.play(FadeIn(end), run_time=0.6)")
    lines.append("        self.wait(0.6)")

    with open(scene_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return scene_file


def _render_with_manim(job_id: str, scene_file: str, scene_class: str, quality: str) -> str:
    job_media_dir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(job_media_dir, exist_ok=True)

    qflag = "-ql" if quality == "low" else "-qm"

    cmd = [
        "manim",
        qflag,
        "--media_dir",
        job_media_dir,
        scene_file,
        scene_class,
    ]

    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if completed.returncode != 0:
        raise RuntimeError(f"Manim failed:\n{completed.stdout}")

    mp4s = glob.glob(os.path.join(job_media_dir, "**", "*.mp4"), recursive=True)
    if not mp4s:
        raise RuntimeError(f"Manim succeeded but no mp4 found under {job_media_dir}")

    mp4s.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return mp4s[0]


@celery.task(name="tasks.learn_pipeline.generate_lesson_video")
def generate_lesson_video(
    *,
    job_id: str,
    topic: str,
    user_goal: Optional[str] = None,
    preferred_language: Optional[str] = "English",
    student_state: Optional[Dict[str, Any]] = None,
    quality: str = "low",
    rag_collection_id: Optional[str] = None,  # ✅ NEW
) -> dict:
    """
    Pipeline:
      1) (Optional) Pull RAG context from user-uploaded docs
      2) LLM generates segments JSON
      3) Write segments.json + script.txt (+ context.txt)
      4) Generate a manim scene python file
      5) Render mp4 via manim
      6) Generate TTS narration (mp3)
      7) Merge audio into mp4
    """
    goal = user_goal or "Learn clearly with intuition and examples."
    lang = preferred_language or "English"
    state = student_state or {}

    # ---------- 0) Optional RAG context ----------
    rag_context = ""
    rag_error = None
    if rag_collection_id:
        try:
            from services.rag_service import query_collection
            rag_context = query_collection(
                collection_id=rag_collection_id,
                question=f"{topic}\nGoal: {goal}",
                top_k=5,
                max_chars=1800,
            )
        except Exception as e:
            rag_error = f"{type(e).__name__}: {e}"
            rag_context = ""

    system = f"You are an expert tutor. Output VALID JSON only. Language: {lang}."

    user = f"""
Topic: {topic}
User goal: {goal}
StudentState (may be empty): {json.dumps(state, ensure_ascii=False)}
""".strip()

    if rag_context:
        user += "\n\nReference material (use if relevant, do not hallucinate beyond it):\n"
        user += rag_context

    user += """
\n\nCreate 6 to 10 learning segments that explain the topic from the right level.
Each segment should have:
- id: 1..N
- title: short
- narration: what teacher would say (2-5 sentences)
- on_screen: what should appear on screen (short, readable text)
- duration_sec: 3..10 (keep total video short)

Return JSON: { "segments": [ ... ] }
""".strip()

    # ---------- 1) Generate segments with retries ----------
    prompt_base = user
    last_err = None
    data = None

    for _attempt in range(1, 4):
        try:
            data = chat_json(
                system=system.strip(),
                user=prompt_base
                + (
                    "\n\nSTRICT REQUIREMENTS:\n"
                    "- Output JSON only. No markdown.\n"
                    "- segments must be an array of 6 to 10 objects.\n"
                    "- EACH segment object MUST contain ALL keys: id,title,narration,on_screen,duration_sec.\n"
                    "- duration_sec must be a number between 3 and 10 for EVERY segment.\n"
                    "- Do NOT output any standalone objects like {\"duration_sec\":8}.\n"
                ),
                schema_name="lesson_segments",
                schema=SEGMENTS_SCHEMA,
                strict=True,
                temperature=0.2,
            )
            break
        except Exception as e:
            last_err = e
            prompt_base = (
                user
                + f"\n\nYour previous output failed schema validation: {str(e)[:250]}..."
                + "\nRegenerate from scratch and follow the schema EXACTLY."
            )

    if data is None:
        raise RuntimeError(f"Failed to generate valid segments after retries: {last_err}")

    segments = data["segments"]

    # ---------- 2) Write artifacts ----------
    job_dir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    segments_path = os.path.join(job_dir, "segments.json")
    with open(segments_path, "w", encoding="utf-8") as f:
        json.dump({"segments": segments}, f, ensure_ascii=False, indent=2)

    script_text = "\n\n".join([f"{s['id']}. {s['title']}\n{s['narration']}" for s in segments])
    script_path = os.path.join(job_dir, "script.txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script_text)

    # Save context used (debug)
    if rag_context:
        with open(os.path.join(job_dir, "context.txt"), "w", encoding="utf-8") as f:
            f.write(rag_context)

    # ---------- 3) Generate Manim scene file ----------
    scene_class = f"LessonScene_{job_id.replace('-', '_')}"
    scene_file = _write_scene_file(job_id=job_id, scene_class=scene_class, topic=topic, segments=segments)

    # ---------- 4) Render ----------
    video_path = _render_with_manim(
        job_id=job_id,
        scene_file=scene_file,
        scene_class=scene_class,
        quality=quality,
    )

    # ---------- 5-6) TTS + merge into MP4 (MVP) ----------
    audio_path = None
    final_video_path = video_path
    tts_error = None

    try:
        from services.tts_service import generate_and_merge_narration

        audio_path, final_video_path = generate_and_merge_narration(
            segments=segments,
            preferred_language=preferred_language,
            job_dir=job_dir,
            video_path=video_path,
        )
    except Exception as e:
        # MVP: don't fail lesson generation if TTS fails
        tts_error = f"{type(e).__name__}: {e}"

    return {
        "status": "COMPLETED",
        "job_id": job_id,
        "topic": topic,
        "rag_collection_id": rag_collection_id,
        "rag_error": rag_error,
        "segments_path": segments_path,
        "script_path": script_path,
        "scene_file": scene_file,
        "scene_class": scene_class,
        "video_path": video_path,
        "audio_path": audio_path,
        "final_video_path": final_video_path,  # if TTS fails, same as video_path
        "tts_error": tts_error,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
