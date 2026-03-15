import os
import json
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

MODEL_DEFAULT = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

_client: Optional[Groq] = None


def _get_client() -> Groq:
    global _client
    if _client is not None:
        return _client

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is missing/empty inside this process. "
            "Set it in .env or docker-compose environment and recreate containers."
        )

    _client = Groq(api_key=api_key)
    return _client


def chat_json(
    *,
    system: str,
    user: str,
    schema_name: str,
    schema: dict,
    strict: bool = True,
    model: str = MODEL_DEFAULT,
    temperature: float = 0.2,
    max_tokens: int = 1200,
) -> Dict[str, Any]:
    client = _get_client()

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": strict,
                "schema": schema,
            },
        },
    )

    content = (resp.choices[0].message.content or "").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Model returned invalid JSON. schema_name={schema_name}. "
            f"First 500 chars:\n{content[:500]}"
        ) from e


def chat_text(
    *,
    messages: List[Dict[str, str]],
    model: str = MODEL_DEFAULT,
    temperature: float = 0.5,
    max_tokens: int = 1200,
) -> str:
    client = _get_client()

    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()
