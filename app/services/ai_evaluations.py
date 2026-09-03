from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import AiEvaluation
from app.services.llm import LlmCompletion


def record_ai_evaluation(
    db: Session | None,
    *,
    user_id: str | None,
    alert_id: str | None,
    kind: str,
    decision: str,
    rationale: str | None = None,
    output: dict[str, Any] | None = None,
    completion: LlmCompletion | None = None,
) -> AiEvaluation | None:
    if db is None or not user_id:
        return None
    row = AiEvaluation(
        user_id=user_id,
        alert_id=alert_id,
        kind=kind,
        decision=decision,
        rationale=(rationale or None),
        output_json=json.dumps(output) if output is not None else None,
        model=completion.model if completion else None,
        generation_id=completion.generation_id if completion else None,
        prompt_tokens=completion.prompt_tokens if completion else None,
        completion_tokens=completion.completion_tokens if completion else None,
        total_tokens=completion.total_tokens if completion else None,
        cost_usd=completion.cost_usd if completion else None,
        latency_ms=completion.latency_ms if completion else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
