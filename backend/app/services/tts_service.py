from __future__ import annotations

import os
import subprocess
from typing import Optional, Tuple

from gtts import gTTS


def build_narration_text(segments: list[dict]) -> str:
    parts = []
    for s in segments or []:
        t = (s.get('narration') or '').strip()
        if t:
            parts.append(t)
    return '\\n\\n'.join(parts).strip()


def generate_tts_mp3(text: str, out_mp3_path: str, lang: str = 'en') -> str:
    os.makedirs(os.path.dirname(out_mp3_path), exist_ok=True)
    tts = gTTS(text=text, lang=lang)
    tts.save(out_mp3_path)
    return out_mp3_path


def merge_audio_with_video(video_path: str, audio_path: str, out_video_path: str) -> str:
    os.makedirs(os.path.dirname(out_video_path), exist_ok=True)

    cmd = [
        'ffmpeg', '-y',
        '-i', video_path,
        '-i', audio_path,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-shortest',
        '-movflags', '+faststart',
        out_video_path,
    ]

    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f'ffmpeg merge failed:\\n{completed.stdout}')

    return out_video_path


def tts_lang_from_preferred_language(preferred_language: Optional[str]) -> str:
    lang = (preferred_language or 'English').strip().lower()
    if lang in ('english', 'en'):
        return 'en'
    if lang in ('hindi', 'hi'):
        return 'hi'
    if lang in ('telugu', 'te'):
        return 'te'
    return 'en'


def generate_and_merge_narration(
    *,
    segments: list[dict],
    preferred_language: Optional[str],
    job_dir: str,
    video_path: str,
) -> Tuple[str, str]:
    narration_text = build_narration_text(segments)
    if not narration_text:
        raise ValueError('No narration text found in segments.')

    audio_dir = os.path.join(job_dir, 'audio')
    audio_path = os.path.join(audio_dir, 'narration.mp3')

    gtts_lang = tts_lang_from_preferred_language(preferred_language)
    generate_tts_mp3(narration_text, audio_path, lang=gtts_lang)

    final_video_path = os.path.join(job_dir, 'final_with_audio.mp4')
    merge_audio_with_video(video_path, audio_path, final_video_path)

    return audio_path, final_video_path
