# backend/app/agents/quiz_agent.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from services.groq_client import chat_json


MCQ_QUESTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 2},
        "type": {"type": "string", "enum": ["mcq"]},
        "difficulty": {"type": "string", "enum": ["EASY", "MEDIUM", "HARD"]},
        "skill": {"type": "string"},
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
    "required": [
        "id",
        "type",
        "difficulty",
        "skill",
        "prompt",
        "options",
        "correct_answer",
        "explanation",
    ],
    "additionalProperties": False,
}

QUIZ_BATCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": MCQ_QUESTION_SCHEMA,
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}


# -----------------------------
# Skill key helpers (MUST align with adaptive.py + quiz.py)
# -----------------------------
def _skill_key(topic: str, skill: str) -> str:
    """
    Canonical mastery key: "<Topic>::<Skill>"
    If already includes "::", keep it as-is.
    """
    t = (topic or "Topic").strip()
    s = (skill or "Core").strip()
    if "::" in s:
        return s
    return f"{t}::{s}"


def _display_skill(key: str) -> str:
    """
    "Binary Search::Core" -> "Core"
    """
    return key.split("::", 1)[1] if "::" in key else key


def _normalize_weakest_skills(topic: str, weakest_skills: Any) -> List[str]:
    """
    Accepts weakest_skills as:
      - ["Topic::Skill", ...] (canonical)
      - ["Skill", ...] (display)
      - "Skill" or "Topic::Skill" (string)
    Returns a list of canonical keys.
    """
    if weakest_skills is None:
        return []

    if isinstance(weakest_skills, str):
        weakest_skills = [weakest_skills]

    if not isinstance(weakest_skills, list):
        return []

    out: List[str] = []
    for s in weakest_skills:
        if not isinstance(s, str):
            continue
        s = s.strip()
        if not s:
            continue
        out.append(_skill_key(topic, s))
    return out


# -----------------------------
# Context helpers
# -----------------------------
def _segments_to_context(segments: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for s in segments or []:
        sid = s.get("id")
        title = (s.get("title") or "").strip()
        narration = (s.get("narration") or "").strip()
        if not (title or narration):
            continue
        lines.append(f"- Segment {sid}: {title}")
        if narration:
            lines.append(f"  Notes: {narration[:300]}")
    return "\n".join(lines).strip()


def _normalize_options(options: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    cleaned = []
    seen = set()
    for opt in options or []:
        oid = opt.get("id")
        text = (opt.get("text") or "").strip()
        if oid in order and oid not in seen and text:
            cleaned.append({"id": oid, "text": text})
            seen.add(oid)

    cleaned.sort(key=lambda x: order.get(x["id"], 999))
    return cleaned


def _validate_mcq_locally(q: Dict[str, Any]) -> None:
    opts = _normalize_options(q.get("options") or [])
    if len(opts) != 4:
        raise ValueError("options must contain exactly 4 items with ids A,B,C,D")

    q["options"] = opts

    ca = q.get("correct_answer")
    if ca not in {"A", "B", "C", "D"}:
        raise ValueError("correct_answer must be one of A/B/C/D")

    ids = [o["id"] for o in opts]
    if set(ids) != {"A", "B", "C", "D"}:
        raise ValueError("options ids must be exactly A,B,C,D")


def _fallback_mcq(
    *,
    q_index: int,
    topic: str,
    difficulty_hint: str,
    skill_focus_key: str,
) -> Dict[str, Any]:
    skill_display = _display_skill(skill_focus_key)
    level = (difficulty_hint or "EASY").upper()

    # Deterministic fallback so quiz generation still succeeds during upstream rate limits.
    option_sets = [
        (
            "B",
            [
                "Ignore the lesson structure and guess from keywords only.",
                "Apply the lesson's core rule to a concrete example before choosing.",
                "Choose the longest answer because it sounds most detailed.",
                "Skip constraints and rely on intuition alone.",
            ],
            f"Best practice for {skill_display} is to apply the core rule to an example.",
        ),
        (
            "C",
            [
                "Start from edge cases first and never define the main concept.",
                "Memorize definitions without checking how they are used.",
                "Identify the key relationship, then test it on the current scenario.",
                "Treat all options as equivalent if they share terminology.",
            ],
            f"The strongest approach is to identify and apply the key relationship for {skill_display}.",
        ),
        (
            "A",
            [
                "Use the stated constraints and update your reasoning step by step.",
                "Prefer an answer that adds assumptions not present in the lesson.",
                "Optimize for speed before checking correctness.",
                "Ignore intermediate checks and jump to a final claim.",
            ],
            f"For {skill_display}, you should follow constraints and update reasoning step by step.",
        ),
        (
            "D",
            [
                "Select a choice based on wording style instead of meaning.",
                "Anchor on prior guess and avoid revisiting assumptions.",
                "Skip verification when two options appear similar.",
                "Compare options against the lesson objective and eliminate mismatches.",
            ],
            f"Comparing options against the lesson objective is the most reliable method for {skill_display}.",
        ),
    ]
    picked = option_sets[(q_index - 1) % len(option_sets)]
    correct, texts, explanation = picked

    q = {
        "id": f"Q{q_index}",
        "type": "mcq",
        "difficulty": level,
        "skill": skill_focus_key,
        "prompt": f"[Fallback] In {topic}, which option best demonstrates {skill_display} at {level} level?",
        "options": [
            {"id": "A", "text": texts[0]},
            {"id": "B", "text": texts[1]},
            {"id": "C", "text": texts[2]},
            {"id": "D", "text": texts[3]},
        ],
        "correct_answer": correct,
        "explanation": explanation,
    }
    _validate_mcq_locally(q)
    return q


# -----------------------------
# Adaptive sampling helpers
# -----------------------------
def _pick_difficulties(num_questions: int, target_level: str) -> List[str]:
    """
    Adaptive difficulty mix.
    - EASY: mostly EASY + few MEDIUM
    - MEDIUM: mix EASY/MEDIUM + few HARD
    - HARD: mostly HARD + some MEDIUM
    """
    target = (target_level or "EASY").upper()
    diffs: List[str] = []

    if target == "HARD":
        for i in range(num_questions):
            diffs.append("HARD" if i < int(num_questions * 0.7) else "MEDIUM")
        return diffs

    if target == "MEDIUM":
        for i in range(num_questions):
            if i < int(num_questions * 0.2):
                diffs.append("EASY")
            elif i < int(num_questions * 0.85):
                diffs.append("MEDIUM")
            else:
                diffs.append("HARD")
        return diffs

    # EASY default
    for i in range(num_questions):
        diffs.append("EASY" if i < int(num_questions * 0.75) else "MEDIUM")
    return diffs


def _pick_skill_for_question(i: int, skill_keys: List[str], topic: str) -> str:
    """
    Returns a canonical key (Topic::Skill).
    """
    if skill_keys:
        return skill_keys[(i - 1) % len(skill_keys)]
    return _skill_key(topic, "Core")


# -----------------------------
# MCQ generation
# -----------------------------
def _generate_one_mcq(
    *,
    q_index: int,
    topic: str,
    preferred_language: str,
    user_goal: str,
    student_state: Dict[str, Any],
    context: str,
    difficulty_hint: str,
    skill_focus_key: str,      # canonical: Topic::Skill
    skill_focus_display: str,  # display: Skill
) -> Dict[str, Any]:
    system = (
        "You are an expert tutor who writes quizzes. "
        f"Output VALID JSON only. Language: {preferred_language}."
    )

    user = f"""
Topic: {topic}
User goal: {user_goal}

Adaptive target:
- Focus skill: {skill_focus_display}
- Difficulty: {difficulty_hint}

StudentState (for reference):
{student_state}

Lesson context (use this, do not invent beyond it):
{context}

Create 1 multiple-choice question (MCQ).
Rules:
- type must be "mcq"
- EXACTLY 4 options with ids A,B,C,D (always all four)
- correct_answer MUST be one of A/B/C/D
- Provide a short explanation
- Make it practical and aligned to the lesson
- Ensure the question is specifically about the focus skill

Return ONLY JSON for one question object.
Also set id to "Q{q_index}".
""".strip()

    prompt = user
    last_err: Optional[Exception] = None

    for _attempt in range(1, 5):
        try:
            q = chat_json(
                system=system,
                user=prompt
                + (
                    "\n\nSTRICT REQUIREMENTS:\n"
                    "- Output JSON only. No markdown.\n"
                    "- Include ALL keys: id,type,difficulty,skill,prompt,options,correct_answer,explanation.\n"
                    "- options must have exactly 4 items with ids A,B,C,D.\n"
                    "- correct_answer must be A/B/C/D.\n"
                ),
                schema_name="mcq_question",
                schema=MCQ_QUESTION_SCHEMA,
                strict=True,
                temperature=0.1,
            )

            _validate_mcq_locally(q)

            # ✅ Enforce canonical key for mastery tracking
            q["skill"] = skill_focus_key
            q["difficulty"] = (difficulty_hint or q.get("difficulty") or "EASY").upper()
            q["id"] = f"Q{q_index}"
            return q

        except Exception as e:
            last_err = e
            prompt = (
                user
                + f"\n\nYour previous output failed validation: {str(e)[:220]}..."
                + "\nRegenerate from scratch and follow the schema EXACTLY."
            )

    raise RuntimeError(f"Failed to generate question Q{q_index} after retries: {last_err}")


def _generate_batch_mcq(
    *,
    topic: str,
    preferred_language: str,
    user_goal: str,
    student_state: Dict[str, Any],
    context: str,
    question_specs: List[Dict[str, Any]],  # [{index, skill_key, skill_display, difficulty}]
) -> List[Dict[str, Any]]:
    """
    Generate all quiz questions in a single LLM call.
    question_specs drives per-question skill/difficulty — same adaptive logic, one round-trip.
    """
    num_questions = len(question_specs)

    spec_lines = "\n".join(
        f"  Q{s['index']}: id=\"Q{s['index']}\", skill=\"{s['skill_display']}\", difficulty={s['difficulty']}"
        for s in question_specs
    )

    system = (
        "You are an expert tutor who writes quizzes. "
        f"Output VALID JSON only. Language: {preferred_language}."
    )

    user = f"""
Topic: {topic}
User goal: {user_goal}

StudentState (for reference):
{student_state}

Lesson context (use this, do not invent beyond it):
{context}

Generate exactly {num_questions} multiple-choice questions (MCQ).
Per-question requirements (follow exactly):
{spec_lines}

Rules for EVERY question:
- type must be "mcq"
- EXACTLY 4 options with ids A, B, C, D
- correct_answer MUST be one of A/B/C/D
- Provide a short explanation
- Make it practical and aligned to the lesson
- Each question must be distinct — no duplicates or paraphrases
- Set id exactly as specified above (Q1, Q2, ...)
- Set skill exactly as the display skill name specified above
- Set difficulty exactly as specified above

Return ONLY a JSON object: {{"questions": [...]}} with {num_questions} items.
""".strip()

    last_err: Optional[Exception] = None
    for _attempt in range(1, 3):
        try:
            batch = chat_json(
                system=system,
                user=user + (
                    "\n\nSTRICT REQUIREMENTS:\n"
                    "- Output JSON only. No markdown.\n"
                    f"- questions array must have EXACTLY {num_questions} items.\n"
                    "- Each item needs: id,type,difficulty,skill,prompt,options,correct_answer,explanation.\n"
                    "- options must have exactly 4 items with ids A,B,C,D.\n"
                    "- correct_answer must be A/B/C/D.\n"
                ),
                schema_name="quiz_batch",
                schema=QUIZ_BATCH_SCHEMA,
                strict=True,
                temperature=0.1,
                max_tokens=num_questions * 550,
            )

            raw_questions = batch.get("questions") or []
            if len(raw_questions) != num_questions:
                raise ValueError(
                    f"Expected {num_questions} questions, got {len(raw_questions)}"
                )

            # Validate and enforce canonical values per question
            validated: List[Dict[str, Any]] = []
            for i, (q, spec) in enumerate(zip(raw_questions, question_specs)):
                _validate_mcq_locally(q)
                q["skill"] = spec["skill_key"]
                q["difficulty"] = spec["difficulty"].upper()
                q["id"] = f"Q{spec['index']}"
                validated.append(q)

            return validated

        except Exception as e:
            last_err = e

    raise RuntimeError(f"Batch quiz generation failed after retries: {last_err}")


def generate_quiz_full(
    *,
    topic: str,
    user_goal: str,
    preferred_language: str,
    student_state: Dict[str, Any],
    segments: List[Dict[str, Any]],
    num_questions: int = 8,
    rag_context: str = "",
) -> Dict[str, Any]:
    preferred_language = preferred_language or "English"
    student_state = student_state or {}
    user_goal = user_goal or "Learn and test understanding."

    ctx = _segments_to_context(segments)
    if rag_context:
        ctx = f"{ctx}\n\nRAG context:\n{rag_context}".strip()
    if not ctx:
        ctx = "- No lesson context available."

    # ✅ adaptive controls
    target_level = (student_state.get("difficulty_level") or "EASY").upper()

    # weakest_skills can arrive as ["Topic::Skill"] OR ["Skill"]
    weakest_skills_raw = student_state.get("weakest_skills") or []
    weakest_skill_keys = _normalize_weakest_skills(topic, weakest_skills_raw)

    difficulties = _pick_difficulties(num_questions, target_level)

    # Build per-question specs (same adaptive logic as before)
    question_specs = []
    for i in range(1, num_questions + 1):
        skill_key = _pick_skill_for_question(i, weakest_skill_keys, topic)
        question_specs.append({
            "index": i,
            "skill_key": skill_key,
            "skill_display": _display_skill(skill_key),
            "difficulty": difficulties[i - 1],
        })

    # --- Batch path: one API call for all questions ---
    try:
        questions = _generate_batch_mcq(
            topic=topic,
            preferred_language=preferred_language,
            user_goal=user_goal,
            student_state=student_state,
            context=ctx,
            question_specs=question_specs,
        )
        return {"topic": topic, "questions": questions}
    except Exception:
        pass  # fall through to sequential

    # --- Sequential fallback: one API call per question ---
    questions = []
    for spec in question_specs:
        try:
            q = _generate_one_mcq(
                q_index=spec["index"],
                topic=topic,
                preferred_language=preferred_language,
                user_goal=user_goal,
                student_state=student_state,
                context=ctx,
                difficulty_hint=spec["difficulty"],
                skill_focus_key=spec["skill_key"],
                skill_focus_display=spec["skill_display"],
            )
        except Exception as exc:
            q = _fallback_mcq(
                q_index=spec["index"],
                topic=topic,
                difficulty_hint=spec["difficulty"],
                skill_focus_key=spec["skill_key"],
            )
            print(
                f"[quiz_agent] fallback question used for Q{spec['index']} "
                f"topic='{topic}': {type(exc).__name__}: {exc}"
            )
        questions.append(q)

    return {"topic": topic, "questions": questions}


# -----------------------------
# Grading
# -----------------------------
def grade_quiz(
    *,
    topic: str,
    preferred_language: str,
    student_state: Dict[str, Any],
    quiz_full: Dict[str, Any],
    answers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    qmap = {q.get("id"): q for q in (quiz_full.get("questions") or [])}
    results: List[Dict[str, Any]] = []

    for a in answers or []:
        qid = a.get("question_id")
        ans = (a.get("answer") or "").strip()
        q = qmap.get(qid)

        if not q:
            results.append(
                {"question_id": qid, "correct": False, "score": 0.0, "feedback": "Question not found in quiz."}
            )
            continue

        if q.get("type") != "mcq":
            results.append(
                {
                    "question_id": qid,
                    "correct": False,
                    "score": 0.0,
                    "feedback": "Unsupported question type in MVP (only mcq supported).",
                }
            )
            continue

        correct = (q.get("correct_answer") or "").strip()
        is_correct = ans.upper() == correct.upper()

        if is_correct:
            results.append({"question_id": qid, "correct": True, "score": 1.0, "feedback": "Correct."})
        else:
            exp = (q.get("explanation") or "").strip()
            results.append(
                {
                    "question_id": qid,
                    "correct": False,
                    "score": 0.0,
                    "feedback": f"Incorrect. Correct answer is {correct}. {exp}",
                }
            )

    overall = (sum(float(r.get("score", 0.0)) for r in results) / len(results)) if results else 0.0
    return {"topic": topic, "overall_score": overall, "results": results}
