from __future__ import annotations

from typing import Any, Dict, List, Optional


def ok(*, result: Any = None, state: str = "SUCCESS", warnings: Optional[List[str]] = None, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"state": state, "error": None, "result": result}
    payload["warnings"] = warnings or []
    payload.update(extra)
    return payload


def fail(*, state: str = "FAILURE", error: str, result: Any = None, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"state": state, "error": error, "result": result, "warnings": []}
    payload.update(extra)
    return payload
