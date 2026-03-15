from __future__ import annotations

import os
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

STATE_DIR = os.getenv("STATE_DIR", "/app/app/storage/state")


def _session_dir(session_id: str) -> str:
    return os.path.join(STATE_DIR, "sessions", session_id)


def _state_path(session_id: str) -> str:
    return os.path.join(_session_dir(session_id), "student_state.json")


def _history_path(session_id: str) -> str:
    return os.path.join(_session_dir(session_id), "history.json")


def _read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def create_session(
    *,
    topic: str,
    preferred_language: str = "English",
    rag_collection_id: Optional[str] = None,
) -> str:
    session_id = uuid.uuid4().hex
    state = {
        "session_id": session_id,
        "topic": topic,
        "preferred_language": preferred_language,
        "rag_collection_id": rag_collection_id,
        "mastery": {},  # skill -> 0..1
        "difficulty_level": "MEDIUM",  # EASY/MEDIUM/HARD
        "created_at": datetime.utcnow().isoformat() + "Z",
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    _write_json(_state_path(session_id), state)
    _write_json(_history_path(session_id), [])
    return session_id


def load_state(session_id: str) -> Dict[str, Any]:
    state = _read_json(_state_path(session_id), default=None)
    if state is None:
        raise FileNotFoundError(f"session_id not found: {session_id}")
    return state


def save_state(session_id: str, state: Dict[str, Any]) -> None:
    state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    _write_json(_state_path(session_id), state)


def append_history(session_id: str, item: Dict[str, Any]) -> None:
    history: List[Dict[str, Any]] = _read_json(_history_path(session_id), default=[])
    history.append(item)
    _write_json(_history_path(session_id), history)


def update_state_from_quiz(
    *,
    state: Dict[str, Any],
    quiz_full: Dict[str, Any],
    answers: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Updates mastery per skill using deterministic scoring.
    Returns: (new_state, summary)
    """
    mastery: Dict[str, float] = dict(state.get("mastery") or {})

    # qid -> (skill, correct_answer)
    qmap: Dict[str, Dict[str, Any]] = {q.get("id"): q for q in (quiz_full.get("questions") or [])}

    # user answers map
    amap: Dict[str, str] = {a.get("question_id"): (a.get("answer") or "").strip().upper() for a in (answers or [])}

    per_skill_total: Dict[str, int] = {}
    per_skill_correct: Dict[str, int] = {}

    for qid, q in qmap.items():
        skill = (q.get("skill") or "general").strip() or "general"
        correct = (q.get("correct_answer") or "").strip().upper()
        given = amap.get(qid)

        per_skill_total[skill] = per_skill_total.get(skill, 0) + 1
        if given and correct and given == correct:
            per_skill_correct[skill] = per_skill_correct.get(skill, 0) + 1

    # Update mastery: simple EMA-like adjustment
    # correct => +0.08, wrong/missing => -0.12 (clamped 0..1)
    for skill, total in per_skill_total.items():
        correct_n = per_skill_correct.get(skill, 0)
        acc = correct_n / total if total else 0.0

        prev = float(mastery.get(skill, 0.5))
        delta = (0.08 * acc) - (0.12 * (1.0 - acc))
        newv = max(0.0, min(1.0, prev + delta))
        mastery[skill] = round(newv, 3)

    # Update difficulty level based on overall mastery / last score behavior
    avg_mastery = round(sum(mastery.values()) / len(mastery), 3) if mastery else 0.5
    if avg_mastery >= 0.75:
        level = "HARD"
    elif avg_mastery <= 0.45:
        level = "EASY"
    else:
        level = "MEDIUM"

    state["mastery"] = mastery
    state["difficulty_level"] = level

    # Pick weakest skills
    weakest = sorted(mastery.items(), key=lambda kv: kv[1])[:3]
    weakest_skills = [s for s, _ in weakest]

    summary = {
        "avg_mastery": avg_mastery,
        "difficulty_level": level,
        "weakest_skills": weakest_skills,
    }
    return state, summary
