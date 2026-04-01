from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, Optional

from gtts import gTTS


def tts_lang_from_preferred_language(preferred_language: Optional[str]) -> str:
    lang = (preferred_language or "English").strip().lower()
    if lang in ("english", "en"):
        return "en"
    if lang in ("hindi", "hi"):
        return "hi"
    if lang in ("telugu", "te"):
        return "te"
    return "en"


def generate_tts_mp3(text: str, out_mp3_path: str, lang: str = "en") -> str:
    os.makedirs(os.path.dirname(out_mp3_path), exist_ok=True)
    tts = gTTS(text=text, lang=lang)
    tts.save(out_mp3_path)
    return out_mp3_path


def probe_media_duration(path: str) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe failed:\n{completed.stdout}")
    return float((completed.stdout or "0").strip() or 0.0)


def ensure_video_duration(video_path: str, target_duration: float, out_path: str) -> str:
    cur = probe_media_duration(video_path)
    target = max(0.5, float(target_duration))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # If shorter, freeze last frame; if longer, trim.
    if cur < target - 0.03:
        pad = round(target - cur + 0.02, 3)
        vf = f"tpad=stop_mode=clone:stop_duration={pad}"
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path]
    elif cur > target + 0.03:
        cmd = ["ffmpeg", "-y", "-i", video_path, "-t", f"{target:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path]
    else:
        cmd = ["ffmpeg", "-y", "-i", video_path, "-c:v", "copy", out_path]

    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=240,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg ensure_video_duration failed:\n{completed.stdout}")
    return out_path


def merge_audio_with_video(video_path: str, audio_path: str, out_video_path: str) -> str:
    os.makedirs(os.path.dirname(out_video_path), exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        out_video_path,
    ]
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=240,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg merge failed:\n{completed.stdout}")
    return out_video_path


def concat_media_files(inputs: list[str], out_path: str) -> str:
    if not inputs:
        raise ValueError("No inputs to concat")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    list_file = os.path.join(os.path.dirname(out_path), "_concat_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in inputs:
            safe = p.replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out_path]
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed:\n{completed.stdout}")
    return out_path


def build_segment_audio_assets(*, segments: list[dict], preferred_language: Optional[str], audio_dir: str) -> list[Dict[str, Any]]:
    gtts_lang = tts_lang_from_preferred_language(preferred_language)
    os.makedirs(audio_dir, exist_ok=True)
    assets: list[Dict[str, Any]] = []

    for idx, seg in enumerate(segments or [], start=1):
        narration = (seg.get("narration") or "").strip()
        if not narration:
            narration = f"Segment {idx}"
        audio_path = os.path.join(audio_dir, f"segment_{idx:02d}.mp3")
        generate_tts_mp3(narration, audio_path, lang=gtts_lang)
        duration = probe_media_duration(audio_path)
        assets.append(
            {
                "segment_index": idx,
                "segment_id": seg.get("id", idx),
                "audio_path": audio_path,
                "audio_duration_sec": round(max(0.8, duration), 3),
                "narration": narration,
                "title": seg.get("title", f"Segment {idx}"),
                "on_screen": seg.get("on_screen", ""),
            }
        )
    return assets
