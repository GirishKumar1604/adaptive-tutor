from __future__ import annotations

from typing import Any, Dict, List, Tuple


def skill_key(topic: str, skill: str) -> str:
    t = (topic or "Topic").strip()
    s = (skill or "Core").strip()
    if "::" in s:
        return s
    return f"{t}::{s}"


def display_skill(key: str) -> str:
    return key.split("::", 1)[1] if "::" in key else key


def normalize_mastery(state: Dict[str, Any]) -> Dict[str, Any]:
    topic = (state.get("topic") or "Topic").strip()
    mastery = state.get("mastery") or {}
    normalized: Dict[str, float] = {}
    for k, v in mastery.items():
        if isinstance(k, str) and "::" in k:
            normalized[k] = float(v)
        else:
            normalized[skill_key(topic, "Core")] = float(v)
    state["mastery"] = normalized
    return state


def avg_mastery(mastery: Dict[str, float]) -> float:
    if not mastery:
        return 0.0
    vals = [float(v) for v in mastery.values()]
    return sum(vals) / len(vals) if vals else 0.0


def recommend_difficulty(avg: float) -> str:
    if avg >= 0.80:
        return "HARD"
    if avg >= 0.55:
        return "MEDIUM"
    return "EASY"


def weakest_skill_keys(mastery: Dict[str, float], top_n: int = 3) -> List[str]:
    if not mastery:
        return []
    return [k for k, _ in sorted(mastery.items(), key=lambda kv: float(kv[1]))[:top_n]]


def weakest_skills(mastery: Dict[str, float], top_n: int = 3) -> List[str]:
    return [display_skill(k) for k in weakest_skill_keys(mastery, top_n=top_n)]


def _compute_deltas_by_difficulty(difficulty: str) -> Tuple[float, float]:
    d = (difficulty or "").upper()
    if d == "HARD":
        return (0.08, -0.10)
    if d == "MEDIUM":
        return (0.06, -0.08)
    return (0.04, -0.06)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def update_mastery_from_quiz(
    *,
    state: Dict[str, Any],
    quiz_full: Dict[str, Any],
    answers: List[Dict[str, Any]],
    graded_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    normalize_mastery(state)
    mastery = state.get("mastery") or {}
    topic = (state.get("topic") or "Topic").strip()
    qmap = {q.get("id"): q for q in (quiz_full.get("questions") or []) if q.get("id")}
    correctness = {r.get("question_id"): bool(r.get("correct") is True) for r in (graded_results or [])}

    for answer in answers or []:
        qid = answer.get("question_id")
        if not qid:
            continue
        q = qmap.get(qid)
        if not q:
            continue
        key = skill_key(topic, (q.get("skill") or "Core"))
        old = float(mastery.get(key, 0.5))
        dc, dw = _compute_deltas_by_difficulty(q.get("difficulty") or "EASY")
        new = old + (dc if correctness.get(qid, False) else dw)
        mastery[key] = round(_clamp01(new), 4)

    avg = avg_mastery(mastery)
    state["difficulty_level"] = recommend_difficulty(avg)
    state["mastery"] = mastery
    return state


def update_mastery_from_remediation(*, state: Dict[str, Any], score: float) -> Dict[str, Any]:
    normalize_mastery(state)
    topic = state.get("topic") or "Topic"
    mastery = state["mastery"]
    key = skill_key(topic, "Core")

    if score >= 0.99:
        delta = 0.08
    elif score >= 0.66:
        delta = 0.04
    elif score >= 0.33:
        delta = 0.01
    else:
        delta = -0.03

    old = float(mastery.get(key, 0.5))
    mastery[key] = round(_clamp01(old + delta), 4)
    avg = avg_mastery(mastery)
    state["difficulty_level"] = recommend_difficulty(avg)
    state["mastery"] = mastery
    return state
