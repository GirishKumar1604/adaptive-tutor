# backend/app/agents/diagnoser_agent.py
import json
from typing import List

from schemas.diagnose import DiagnoseQuestion, StudentState, SubmitDiagnoseRequest
from services.groq_client import chat_json


def _normalize_question_ids(questions: list) -> list:
    for i, q in enumerate(questions, start=1):
        q["id"] = f"Q{i}"
    return questions


def generate_questions(topic: str) -> List[DiagnoseQuestion]:
    system = "You are an educational assessor. Return valid JSON only. No markdown."
    user = f"""
Generate exactly 5 micro-diagnosis questions for the topic: "{topic}"

Rules:
- Keep each question answerable in 1-3 lines.
- Include: 2 conceptual, 2 applied, 1 prereq/preference.
- Provide difficulty: EASY/MEDIUM/HARD.
- Provide skill tag: definition/intuition/application/prereq/preference.
Return JSON as:
{{ "questions": [{{"id","topic","difficulty","skill","prompt"}}, ...] }}
"""

    data = chat_json(
        system=system,
        user=user,
        schema_name="diagnose_questions",
        schema=QUESTIONS_SCHEMA,
        strict=True,
    )

    data["questions"] = _normalize_question_ids(data["questions"])
    return [DiagnoseQuestion(**q) for q in data["questions"]]


def evaluate_and_build_state(req: SubmitDiagnoseRequest) -> StudentState:
    system = "You are an educational evaluator. Return valid JSON only. No markdown."
    answers_json = json.dumps([a.model_dump() for a in req.answers], ensure_ascii=False)

    user = f"""
Topic: {req.topic}
Answers: {answers_json}

Rules:
- Infer correctness per answer and misconceptions from answer text.
- Start theta=0
- Correct: EASY +0.3, MEDIUM +0.5, HARD +0.7
- Wrong: subtract same
- Multiply each update by confidence factor: 1->0.6, 2->0.8, 3->1.0, 4->1.1, 5->1.2
- Clip theta to [-2,2]
- confidence must be between 0 and 1
- Map level: theta<=-0.5 Beginner, -0.5<theta<=0.8 Intermediate, >0.8 Advanced
- Pace: low confidence/unclear => Slow; mixed => Normal; confident+correct => Fast
- Style: story language => Story; step/formula => Formal; example preference => ExampleDriven

Return JSON ONLY matching the StudentState schema.
"""

    data = chat_json(
        system=system,
        user=user,
        schema_name="student_state",
        schema=STUDENT_STATE_SCHEMA,
        strict=True,
    )

    # extra safety clamp
    if "confidence" in data:
        data["confidence"] = max(0.0, min(1.0, float(data["confidence"])))

    return StudentState(**data)


QUESTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "topic": {"type": "string"},
                    "difficulty": {"type": "string", "enum": ["EASY", "MEDIUM", "HARD"]},
                    "skill": {
                        "type": "string",
                        "enum": ["definition", "intuition", "application", "prereq", "preference"],
                    },
                    "prompt": {"type": "string"},
                },
                "required": ["id", "topic", "difficulty", "skill", "prompt"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}

STUDENT_STATE_SCHEMA = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": ["Beginner", "Intermediate", "Advanced"]},
        "pace": {"type": "string", "enum": ["Slow", "Normal", "Fast"]},
        "style": {"type": "string", "enum": ["Story", "Formal", "ExampleDriven"]},
        "theta": {"type": "number", "minimum": -2, "maximum": 2},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"tag": {"type": "string"}, "description": {"type": "string"}},
                "required": ["tag", "description"],
                "additionalProperties": False,
            },
        },
        "needs_prereqs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["level", "pace", "style", "theta", "confidence", "gaps", "needs_prereqs"],
    "additionalProperties": False,
}
