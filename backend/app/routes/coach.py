from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.api_response import fail, ok
from services.groq_client import chat_text
from services.rag_service import query_collection_with_sources

router = APIRouter()


class CoachTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(..., min_length=1, max_length=4000)


class CoachChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    topic: Optional[str] = None
    preferred_language: Optional[str] = "English"
    rag_collection_id: Optional[str] = None
    history: List[CoachTurn] = Field(default_factory=list)


@router.post("/coach/chat")
def coach_chat(req: CoachChatRequest):
    rag_context = ""
    rag_sources: List[Dict[str, Any]] = []
    rag_warning: Optional[str] = None

    if req.rag_collection_id:
        try:
            rag_data = query_collection_with_sources(
                collection_id=req.rag_collection_id,
                question=req.question,
                top_k=4,
                max_chars=1200,
            )
            rag_context = rag_data.get("context") or ""
            rag_sources = rag_data.get("sources") or []
        except Exception as exc:
            rag_warning = f"RAG unavailable: {type(exc).__name__}: {exc}"

    system_prompt = (
        "You are an adaptive learning coach. "
        "Give clear, practical explanations in the user's preferred language. "
        "Keep answers concise but useful, and end with one quick check question."
    )
    if req.topic:
        system_prompt += f" Current learning topic: {req.topic}."
    if req.preferred_language:
        system_prompt += f" Preferred language: {req.preferred_language}."

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for turn in req.history[-8:]:
        messages.append({"role": turn.role, "content": turn.text})

    user_content = req.question
    if rag_context:
        user_content = (
            f"Question: {req.question}\n\n"
            f"Grounding context from notes:\n{rag_context}\n\n"
            "Use this context when relevant."
        )
    messages.append({"role": "user", "content": user_content})

    try:
        answer = chat_text(messages=messages, temperature=0.35, max_tokens=700).strip()
        if not answer:
            answer = "I can help with that. Can you share what part is confusing so I can explain it simply?"
        warnings = [rag_warning] if rag_warning else []
        return ok(
            result={
                "answer": answer,
                "used_rag": bool(rag_context),
                "sources": rag_sources,
            },
            warnings=warnings,
        )
    except Exception as exc:
        return fail(error=f"Coach response failed: {type(exc).__name__}: {exc}")
