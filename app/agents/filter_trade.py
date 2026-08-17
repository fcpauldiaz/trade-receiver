import json

from pydantic import BaseModel

from app.config import settings
from app.models.tables import User
from app.schemas.trade import TradeIntent

SKIP_REASON_MAX = 255
PROMPT_PREFIX = "user prompt: "
UNAVAILABLE_RATIONALE = f"{PROMPT_PREFIX}filter unavailable"


class FilterDecision(BaseModel):
    take: bool
    reason: str = ""


def _normalized_prompt(user: User) -> str | None:
    raw = user.trade_filter_prompt
    if not raw:
        return None
    stripped = raw.strip()
    return stripped or None


def _compact_intent(intent: TradeIntent) -> dict:
    return {
        "action": intent.action,
        "underlying": intent.underlying,
        "option_type": intent.option_type,
        "strike": str(intent.strike),
        "expiration": intent.expiration.isoformat() if intent.expiration else None,
        "quantity": intent.quantity,
        "order_type": intent.order_type,
        "confidence": intent.confidence,
        "rationale": intent.rationale,
    }


def _skip(intent: TradeIntent, reason: str) -> TradeIntent:
    text = reason if reason.startswith(PROMPT_PREFIX) else f"{PROMPT_PREFIX}{reason}"
    return intent.model_copy(update={"action": "skip", "rationale": text[:SKIP_REASON_MAX]})


async def apply_trade_filter(intent: TradeIntent, user: User) -> TradeIntent:
    if intent.action == "skip":
        return intent
    prompt = _normalized_prompt(user)
    if prompt is None:
        return intent
    if not settings.openai_api_key:
        return _skip(intent, "filter unavailable")
    try:
        decision = await _filter_with_openai(prompt, intent)
    except Exception:
        return _skip(intent, "filter unavailable")
    if decision.take:
        return intent
    reason = decision.reason.strip() or "skipped by filter"
    return _skip(intent, reason)


async def _filter_with_openai(prompt: str, intent: TradeIntent) -> FilterDecision:
    import httpx

    schema = FilterDecision.model_json_schema()
    user_content = (
        "User rules:\n"
        f"{prompt}\n\n"
        "Parsed trade intent (do not change strike, quantity, or side; only decide take or skip):\n"
        f"{json.dumps(_compact_intent(intent))}\n\n"
        "Schema:\n"
        f"{json.dumps(schema)}"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.ai_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Follow the user's trading rules and return only JSON. "
                            "Set take=true to execute the trade as parsed. "
                            "Set take=false to skip it. "
                            "Do not invent fills or change strike, quantity, or side."
                        ),
                    },
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return FilterDecision.model_validate_json(content)
