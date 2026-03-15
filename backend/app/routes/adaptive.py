# backend/app/routes/adaptive.py
from __future__ import annotations

import os
import json
from datetime import datetime
from typing import Optional, Any, Dict, List, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from celery_app import celery
from services.groq_client import chat_json

router = APIRouter()

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/app/storage/videos")
SESSIONS_DIR = os.path.join(OUTPUT_DIR, "_sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)


# -----------------------------
# Mastery key helpers
# -----------------------------
def _skill_key(topic: str, skill: str) -> str:
    """
    Canonical mastery key format:
      "<Topic>::<Skill>"
    """
    t = (topic or "Topic").strip()
    s = (skill or "Core").strip()
    # If already in Topic::Skill form, keep it
    if "::" in s:
        return s
    return f"{t}::{s}"


def _display_skill(key: str) -> str:
    """
    "Binary Search::Core" -> "Core"
    """
    return key.split("::", 1)[1] if "::" in key else key


def _normalize_mastery_keys(state: Dict[str, Any]) -> None:
    """
    Backward compatibility:
      {"Binary Search": 0.44}  -> {"Binary Search::Core": 0.44}
    Also ensures state["mastery"] exists.
    """
    topic = (state.get("topic") or "Topic").strip()
    mastery = state.get("mastery") or {}

    if not mastery:
        state["mastery"] = {}
        return

    new_mastery: Dict[str, float] = {}
    for k, v in mastery.items():
        if isinstance(k, str) and "::" in k:
            new_mastery[k] = v
        else:
            # old key -> treat as Core for this topic
            new_mastery[_skill_key(topic, "Core")] = v

    state["mastery"] = new_mastery


# -----------------------------
# Session storage helpers
# -----------------------------
def _session_path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")


def _remediation_path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{session_id}_remediation.json")


def _load_session(session_id: str) -> Dict[str, Any]:
    p = _session_path(session_id)
    if not os.path.exists(p):
        raise FileNotFoundError(f"session_id not found: {session_id}")
    with open(p, "r", encoding="utf-8") as f:
        state = json.load(f)

    # ✅ normalize mastery keys for backward compatibility
    _normalize_mastery_keys(state)
    return state


def _write_session(session_id: str, state: Dict[str, Any]) -> None:
    # ✅ ensure we persist normalized format
    _normalize_mastery_keys(state)
    p = _session_path(session_id)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _write_json(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# -----------------------------
# Mastery helpers
# -----------------------------
def _weakest_skill_keys(mastery: Dict[str, float], top_n: int = 3) -> List[str]:
    if not mastery:
        return []
    return [k for k, _ in sorted(mastery.items(), key=lambda kv: kv[1])[:top_n]]


def _weakest_skills_display(mastery: Dict[str, float], top_n: int = 3) -> List[str]:
    keys = _weakest_skill_keys(mastery, top_n=top_n)
    return [_display_skill(k) for k in keys]


def _avg_mastery(mastery: Dict[str, float]) -> float:
    if not mastery:
        return 0.0
    vals = [float(v) for v in mastery.values() if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _recommend_difficulty(avg: float) -> str:
    # MUST match quiz.py mapping
    if avg >= 0.80:
        return "HARD"
    if avg >= 0.55:
        return "MEDIUM"
    return "EASY"


# -----------------------------
# Schemas for remediation (LLM strict JSON)
# -----------------------------
REMEDIATION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "focus_skills": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
        "difficulty": {"type": "string", "enum": ["EASY", "MEDIUM", "HARD"]},
        "bullets": {"type": "array", "items": {"type": "string"}, "minItems": 6, "maxItems": 6},
        "tiny_example": {"type": "string"},
        "checks": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "prompt": {"type": "string"},
                    "options": {
                        "type": "array",
                        "minItems": 4,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "enum": ["A", "B", "C", "D"]},
                                "text": {"type": "string"},
                            },
                            "required": ["id", "text"],
                            "additionalProperties": False,
                        },
                    },
                    "correct_answer": {"type": "string", "enum": ["A", "B", "C", "D"]},
                    "explanation": {"type": "string"},
                },
                "required": ["id", "prompt", "options", "correct_answer", "explanation"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["topic", "focus_skills", "difficulty", "bullets", "tiny_example", "checks"],
    "additionalProperties": False,
}


# -----------------------------
# API Models
# -----------------------------
class AdaptiveStartRequest(BaseModel):
    topic: str = Field(..., min_length=2)
    preferred_language: Optional[str] = "English"
    rag_collection_id: Optional[str] = None
    quality: str = "low"


class AdaptiveStartResponse(BaseModel):
    session_id: str
    job_id: str
    task_id: str


class AdaptiveNextQuizRequest(BaseModel):
    session_id: str = Field(..., min_length=6)
    num_questions: int = 8


class NextActionRequest(BaseModel):
    session_id: str = Field(..., min_length=6)


class NextActionResponse(BaseModel):
    session_id: str
    action: Literal["NEXT_QUIZ", "REMEDIATE", "ADVANCE"]
    recommended_difficulty: Literal["EASY", "MEDIUM", "HARD"]
    avg_mastery: float
    weakest_skills: List[str]  # ✅ display names (UI-friendly)
    message: str
    remediation_prompt: Optional[str] = None


class RemediateRequest(BaseModel):
    session_id: str = Field(..., min_length=6)


# -----------------------------
# Routes
# -----------------------------
@router.post("/adaptive/start", response_model=AdaptiveStartResponse)
def adaptive_start(req: AdaptiveStartRequest):
    session_id = os.urandom(16).hex()
    job_id = os.urandom(16).hex()

    task = celery.send_task(
        "tasks.learn_pipeline.generate_lesson_video",
        kwargs={
            "job_id": job_id,
            "topic": req.topic,
            "preferred_language": req.preferred_language or "English",
            "quality": req.quality or "low",
            "student_state": {},
            "rag_collection_id": req.rag_collection_id,
        },
    )

    now = datetime.utcnow().isoformat() + "Z"
    state = {
        "session_id": session_id,
        "job_id": job_id,
        "topic": req.topic,
        "preferred_language": req.preferred_language or "English",
        "rag_collection_id": req.rag_collection_id,
        # ✅ canonical mastery key format
        "mastery": {_skill_key(req.topic, "Core"): 0.5},
        "difficulty_level": "EASY",
        "created_at": now,
        "updated_at": now,
    }
    _write_session(session_id, state)

    return AdaptiveStartResponse(session_id=session_id, job_id=job_id, task_id=task.id)


@router.get("/adaptive/state/{session_id}")
def adaptive_state(session_id: str):
    try:
        state = _load_session(session_id)
        # Optional: keep state as-is (mastery keys are canonical)
        return {"state": state}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="session_id not found")


@router.post("/adaptive/next-quiz")
def adaptive_next_quiz(req: AdaptiveNextQuizRequest):
    try:
        state = _load_session(req.session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="session_id not found")

    mastery = state.get("mastery") or {}
    weakest_keys = _weakest_skill_keys(mastery, top_n=3)
    topic = state.get("topic") or "Topic"

    student_state = {
        "session_id": req.session_id,
        "difficulty_level": state.get("difficulty_level", "EASY"),
        "mastery": mastery,  # canonical keys
        "weakest_skills": weakest_keys or [_skill_key(topic, "Core")],
    }

    task = celery.send_task(
        "tasks.quiz_pipeline.generate_quiz",
        kwargs={
            "job_id": state["job_id"],
            "topic": state["topic"],
            "preferred_language": state.get("preferred_language") or "English",
            "student_state": student_state,
            "num_questions": req.num_questions,
        },
    )

    state["last_quiz_task_id"] = task.id
    state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    _write_session(req.session_id, state)

    return {
        "session_id": req.session_id,
        "job_id": state["job_id"],
        "topic": state["topic"],
        "task_id": task.id,
        "student_state_used": student_state,
    }


@router.get("/adaptive/quiz/{session_id}")
def adaptive_quiz(session_id: str):
    try:
        state = _load_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="session_id not found")

    job_id = state.get("job_id")
    quiz_path = os.path.join(OUTPUT_DIR, job_id, "quiz.json")
    if not os.path.exists(quiz_path):
        raise HTTPException(status_code=404, detail="quiz.json not ready. Call /adaptive/next-quiz first.")

    return _load_json(quiz_path)


@router.post("/adaptive/next-action", response_model=NextActionResponse)
def adaptive_next_action(req: NextActionRequest):
    """
    Decide what to do NEXT based on current session mastery:
    - If avg_mastery < 0.45 => REMEDIATE
    - If 0.45..0.80 => NEXT_QUIZ
    - If >= 0.80 => ADVANCE (increase difficulty)
    """
    try:
        state = _load_session(req.session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="session_id not found")

    topic = state.get("topic") or "Topic"
    mastery = state.get("mastery") or {}

    avg = _avg_mastery(mastery)
    rec_diff = _recommend_difficulty(avg)

    weakest_keys = _weakest_skill_keys(mastery, top_n=3) or [_skill_key(topic, "Core")]
    weakest_display = [_display_skill(k) for k in weakest_keys]

    remediation_prompt = None
    if avg < 0.45:
        action = "REMEDIATE"
        message = "You’re struggling on the basics. Let’s do a quick focused explanation + 3 easy checks."
        focus = ", ".join(weakest_display or ["Core"])
        remediation_prompt = (
            f"Explain '{topic}' from scratch in 6 bullets, "
            f"with 1 tiny example and 3 quick checkpoint MCQs (easy). "
            f"Focus on: {focus}."
        )
        state["last_remediation_prompt"] = remediation_prompt
    elif avg >= 0.80:
        action = "ADVANCE"
        message = "Great mastery. Let’s level up the difficulty."
    else:
        action = "NEXT_QUIZ"
        message = "Good. Let’s continue with another quiz to improve consistency."

    state["last_action"] = action
    state["difficulty_level"] = rec_diff
    state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    _write_session(req.session_id, state)

    return NextActionResponse(
        session_id=req.session_id,
        action=action,
        recommended_difficulty=rec_diff,
        avg_mastery=round(avg, 4),
        weakest_skills=weakest_display,
        message=message,
        remediation_prompt=remediation_prompt,
    )


@router.post("/adaptive/remediate")
def adaptive_remediate(req: RemediateRequest):
    """
    Generate and store a remediation pack:
    - 6 bullets
    - 1 tiny example
    - 3 checkpoint MCQs (with answers)
    Saved at: OUTPUT_DIR/_sessions/{session_id}_remediation.json
    """
    try:
        state = _load_session(req.session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="session_id not found")

    topic = state.get("topic") or "Topic"
    lang = state.get("preferred_language") or "English"
    difficulty = state.get("difficulty_level") or "EASY"
    mastery = state.get("mastery") or {}

    weakest_keys = _weakest_skill_keys(mastery, top_n=3) or [_skill_key(topic, "Core")]
    focus_skills_display = [_display_skill(k) for k in weakest_keys] or ["Core"]

    prompt = state.get("last_remediation_prompt")
    if not prompt:
        prompt = (
            f"Explain '{topic}' from scratch in 6 bullets, with 1 tiny example "
            f"and 3 quick checkpoint MCQs (easy). Focus on: {', '.join(focus_skills_display)}."
        )

    system = f"You are an expert tutor. Output VALID JSON only. Language: {lang}."

    user = f"""
Topic: {topic}
Focus skills: {focus_skills_display}
Recommended difficulty: {difficulty}
Instruction: {prompt}

Return JSON with:
- topic (string)
- focus_skills (array of 1..3 strings)
- difficulty (EASY/MEDIUM/HARD)
- bullets: exactly 6 short bullets
- tiny_example: 3-6 lines max
- checks: exactly 3 MCQs with {{"id","prompt","options":[A,B,C,D],"correct_answer","explanation"}}

No markdown. JSON only.
""".strip()

    last_err: Optional[Exception] = None
    data: Optional[Dict[str, Any]] = None

    for _attempt in range(1, 4):
        try:
            data = chat_json(
                system=system,
                user=user
                + "\n\nSTRICT REQUIREMENTS:\n"
                + "- Output JSON only. No markdown.\n"
                + "- bullets must be exactly 6 items.\n"
                + "- checks must be exactly 3 items.\n"
                + "- each check must have exactly 4 options with ids A,B,C,D.\n",
                schema_name="remediation_pack",
                schema=REMEDIATION_SCHEMA,
                strict=True,
                temperature=0.2,
            )
            break
        except Exception as e:
            last_err = e
            user = user + f"\n\nYour previous output failed schema validation: {str(e)[:220]}...\nRegenerate from scratch."

    if data is None:
        raise HTTPException(status_code=500, detail=f"Failed to generate remediation after retries: {last_err}")

    remediation = {
        **data,
        "session_id": req.session_id,
        "job_id": state.get("job_id"),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }

    rpath = _remediation_path(req.session_id)
    _write_json(rpath, remediation)

    # record pointer in session
    state["last_remediation_path"] = rpath
    state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    _write_session(req.session_id, state)

    return {"session_id": req.session_id, "remediation": remediation}


@router.get("/adaptive/remediation/{session_id}")
def adaptive_get_remediation(session_id: str):
    try:
        _load_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="session_id not found")

    rpath = _remediation_path(session_id)
    if not os.path.exists(rpath):
        raise HTTPException(status_code=404, detail="remediation not found. Call /adaptive/remediate first.")
    return _load_json(rpath)


class RemediationAnswer(BaseModel):
    question_id: str
    answer: str


class RemediationSubmitRequest(BaseModel):
    session_id: str = Field(..., min_length=6)
    answers: List[RemediationAnswer]


@router.post("/adaptive/remediation/submit")
def adaptive_remediation_submit(req: RemediationSubmitRequest):
    # 1) load session
    try:
        state = _load_session(req.session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="session_id not found")

    topic = state.get("topic") or "Topic"

    # 2) load remediation pack
    rpath = _remediation_path(req.session_id)
    if not os.path.exists(rpath):
        raise HTTPException(status_code=404, detail="remediation not found. Call /adaptive/remediate first.")

    remediation = _load_json(rpath)
    checks = remediation.get("checks") or []
    if not checks:
        raise HTTPException(status_code=400, detail="remediation checks missing")

    # map checks by id (c1/c2/c3)
    cmap = {c.get("id"): c for c in checks}

    # 3) grade
    results = []
    correct_count = 0

    for a in req.answers:
        qid = a.question_id
        ans = (a.answer or "").strip().upper()
        q = cmap.get(qid)

        if not q:
            results.append({"question_id": qid, "correct": False, "score": 0.0, "feedback": "Question not found."})
            continue

        correct = (q.get("correct_answer") or "").strip().upper()
        if ans == correct:
            correct_count += 1
            results.append({"question_id": qid, "correct": True, "score": 1.0, "feedback": "Correct."})
        else:
            exp = (q.get("explanation") or "").strip()
            results.append(
                {
                    "question_id": qid,
                    "correct": False,
                    "score": 0.0,
                    "feedback": f"Incorrect. Correct is {correct}. {exp}",
                }
            )

    total = len(results) if results else 0
    score = (correct_count / total) if total else 0.0

    # 4) update mastery (simple, stable rule)
    # - remediation should move mastery gently
    # - correct all 3 => +0.08
    # - 2/3 => +0.04
    # - 1/3 => +0.01
    # - 0/3 => -0.03
    delta_map = {1.0: 0.08, 0.6667: 0.04, 0.3333: 0.01, 0.0: -0.03}
    rounded = round(score, 4)
    if rounded >= 0.99:
        delta = delta_map[1.0]
    elif rounded >= 0.66:
        delta = delta_map[0.6667]
    elif rounded >= 0.33:
        delta = delta_map[0.3333]
    else:
        delta = delta_map[0.0]

    # ✅ remediation updates Core skill for the topic
    mastery = state.get("mastery") or {}
    key = _skill_key(topic, "Core")

    old = float(mastery.get(key, 0.5))
    new = max(0.0, min(1.0, old + delta))
    mastery[key] = round(new, 4)

    # 5) persist + recompute difficulty
    state["mastery"] = mastery
    avg = _avg_mastery(mastery)
    state["difficulty_level"] = _recommend_difficulty(avg)
    state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    _write_session(req.session_id, state)

    # 6) return next action too (UI friendly weakest skills)
    weakest_keys = _weakest_skill_keys(mastery) or [key]
    weakest_display = [_display_skill(k) for k in weakest_keys]
    rec_diff = state["difficulty_level"]

    if avg < 0.45:
        next_action = "REMEDIATE"
    elif avg >= 0.80:
        next_action = "ADVANCE"
    else:
        next_action = "NEXT_QUIZ"

    return {
        "session_id": req.session_id,
        "score_percent": round(score * 100, 2),
        "correct": correct_count,
        "total": total,
        "results": results,
        "updated_state": state,
        "next": {
            "action": next_action,
            "recommended_difficulty": rec_diff,
            "avg_mastery": round(avg, 4),
            "weakest_skills": weakest_display,
        },
    }


class AdaptiveStepRequest(BaseModel):
    session_id: str = Field(..., min_length=6)
    num_questions: int = 8
    force_remediate: bool = False


@router.post("/adaptive/step")
def adaptive_step(req: AdaptiveStepRequest):
    """
    One-call adaptive step:
    - Decide next action based on mastery
    - If REMEDIATE: ensure remediation exists (generate if missing) and return it
    - Else: start quiz generation and return task_id + quiz/status URLs
    """
    # 1) load session
    try:
        state = _load_session(req.session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="session_id not found")

    topic = state.get("topic") or "Topic"
    mastery = state.get("mastery") or {}
    avg = _avg_mastery(mastery)

    weakest_keys = _weakest_skill_keys(mastery, top_n=3) or [_skill_key(topic, "Core")]
    weakest_display = [_display_skill(k) for k in weakest_keys]

    # 2) decide difficulty + action
    rec_diff = _recommend_difficulty(avg)
    if req.force_remediate or avg < 0.45:
        action = "REMEDIATE"
    elif avg >= 0.80:
        action = "ADVANCE"
    else:
        action = "NEXT_QUIZ"

    # update session difficulty + last_action
    state["difficulty_level"] = rec_diff
    state["last_action"] = action
    state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    _write_session(req.session_id, state)

    # 3) REMEDIATE: generate if missing, then return remediation
    if action == "REMEDIATE":
        rpath = _remediation_path(req.session_id)

        if not os.path.exists(rpath):
            lang = state.get("preferred_language") or "English"
            difficulty = state.get("difficulty_level") or "EASY"

            prompt = state.get("last_remediation_prompt")
            if not prompt:
                prompt = (
                    f"Explain '{topic}' from scratch in 6 bullets, with 1 tiny example "
                    f"and 3 quick checkpoint MCQs (easy). Focus on: {', '.join(weakest_display or ['Core'])}."
                )

            system = f"You are an expert tutor. Output VALID JSON only. Language: {lang}."
            user = f"""
Topic: {topic}
Focus skills: {weakest_display}
Recommended difficulty: {difficulty}
Instruction: {prompt}

Return JSON with:
- topic (string)
- focus_skills (array of 1..3 strings)
- difficulty (EASY/MEDIUM/HARD)
- bullets: exactly 6 short bullets
- tiny_example: 3-6 lines max
- checks: exactly 3 MCQs with {{"id","prompt","options":[A,B,C,D],"correct_answer","explanation"}}

No markdown. JSON only.
""".strip()

            last_err: Optional[Exception] = None
            data: Optional[Dict[str, Any]] = None

            for _attempt in range(1, 4):
                try:
                    data = chat_json(
                        system=system,
                        user=user
                        + "\n\nSTRICT REQUIREMENTS:\n"
                        + "- Output JSON only. No markdown.\n"
                        + "- bullets must be exactly 6 items.\n"
                        + "- checks must be exactly 3 items.\n"
                        + "- each check must have exactly 4 options with ids A,B,C,D.\n",
                        schema_name="remediation_pack",
                        schema=REMEDIATION_SCHEMA,
                        strict=True,
                        temperature=0.2,
                    )
                    break
                except Exception as e:
                    last_err = e
                    user = user + f"\n\nYour previous output failed schema validation: {str(e)[:220]}...\nRegenerate from scratch."

            if data is None:
                raise HTTPException(status_code=500, detail=f"Failed to generate remediation after retries: {last_err}")

            remediation = {
                **data,
                "session_id": req.session_id,
                "job_id": state.get("job_id"),
                "generated_at": datetime.utcnow().isoformat() + "Z",
            }

            _write_json(rpath, remediation)

            state["last_remediation_path"] = rpath
            state["updated_at"] = datetime.utcnow().isoformat() + "Z"
            _write_session(req.session_id, state)

        remediation = _load_json(rpath)
        return {
            "session_id": req.session_id,
            "mode": "REMEDIATE",
            "action": "REMEDIATE",
            "recommended_difficulty": rec_diff,
            "avg_mastery": round(avg, 4),
            "weakest_skills": weakest_display,
            "remediation": remediation,
            "submit_url": "/adaptive/remediation/submit",
            "next_step": "Submit remediation answers, then call /adaptive/step again.",
        }

    # 4) NEXT_QUIZ / ADVANCE: trigger quiz pipeline
    student_state = {
        "session_id": req.session_id,
        "difficulty_level": rec_diff,
        "mastery": mastery,  # canonical keys
        "weakest_skills": weakest_keys,  # canonical keys (machine)
    }

    task = celery.send_task(
        "tasks.quiz_pipeline.generate_quiz",
        kwargs={
            "job_id": state["job_id"],
            "topic": topic,
            "preferred_language": state.get("preferred_language") or "English",
            "student_state": student_state,
            "num_questions": req.num_questions,
        },
    )

    state["last_quiz_task_id"] = task.id
    state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    _write_session(req.session_id, state)

    return {
        "session_id": req.session_id,
        "mode": "QUIZ",
        "action": action,
        "recommended_difficulty": rec_diff,
        "avg_mastery": round(avg, 4),
        "weakest_skills": weakest_display,  # UI friendly
        "job_id": state["job_id"],
        "task_id": task.id,
        "quiz_url": f"/adaptive/quiz/{req.session_id}",
        "status_url": f"/quiz/status/{task.id}",
        "submit_url": "/quiz/submit",
        "note": "Wait for quiz status SUCCESS, fetch quiz_url, then submit answers with session_id + job_id.",
    }
