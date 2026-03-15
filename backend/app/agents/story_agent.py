from __future__ import annotations

from schemas.diagnose import StudentState
from services.groq_client import chat_text


def generate_story_explanation(
    *,
    topic: str,
    student_state: StudentState,
    user_goal: str | None = None,
    preferred_language: str | None = "English",
) -> str:
    lang = preferred_language or "English"
    goal = user_goal or "Learn the topic clearly with intuition and examples."

    style = student_state.style  # Story / Formal / ExampleDriven
    pace = student_state.pace
    level = student_state.level

    system = f"""
You are an expert tutor.
Write in {lang}.
Adapt to the student profile (level/style/pace), and keep it practical.
Do NOT output JSON. Do NOT output markdown fences.
"""

    user = f"""
Topic: {topic}
StudentState: {student_state.model_dump()}

User goal: {goal}

Write:
1) A 2-3 line diagnosis summary (level/style/pace + what that means).
2) A tailored explanation of the topic:
   - If style=Story: use an analogy + simple narrative.
   - If style=Formal: use crisp steps/structure.
   - If style=ExampleDriven: use 2 concrete examples.
3) If gaps exist: address the top 1-3 gaps directly with corrections.
4) If needs_prereqs exist: list them as "Quick prerequisites" with 1-line explanation each.
5) End with "Next steps" (3 bullets) for what the learner should learn next.

Keep it within ~250-450 words.
"""

    return chat_text(
        messages=[
            {"role": "system", "content": system.strip()},
            {"role": "user", "content": user.strip()},
        ],
        temperature=0.5 if pace != "Slow" else 0.3,
    )
