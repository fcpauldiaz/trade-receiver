import json

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.tables import User
from app.schemas.trade import TradeIntent
from app.services.ai_evaluations import record_ai_evaluation
from app.services.llm import chat_json_completion, llm_configured

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
        "asset_class": intent.asset_class,
        "underlying": intent.underlying,
        "option_type": intent.option_type,
        "strike": str(intent.strike),
        "expiration": intent.expiration.isoformat() if intent.expiration else None,
        "quantity": intent.quantity,
        "order_type": intent.order_type,
        "confidence": intent.confidence,
        "rationale": intent.rationale,
        "stop_loss_ticks": intent.stop_loss_ticks,
        "profit_target_ticks": intent.profit_target_ticks,
    }


def _skip(intent: TradeIntent, reason: str) -> TradeIntent:
    text = reason if reason.startswith(PROMPT_PREFIX) else f"{PROMPT_PREFIX}{reason}"
    return intent.model_copy(update={"action": "skip", "rationale": text[:SKIP_REASON_MAX]})


async def apply_trade_filter(
    intent: TradeIntent,
    user: User,
    *,
    db: Session | None = None,
    alert_id: str | None = None,
) -> TradeIntent:
    if intent.action == "skip":
        return intent
    prompt = _normalized_prompt(user)
    if prompt is None:
        return intent
    if not llm_configured():
        return _skip(intent, "filter unavailable")
    try:
        decision = await _filter_with_llm(prompt, intent, db=db, user_id=user.id, alert_id=alert_id)
    except Exception:
        return _skip(intent, "filter unavailable")
    if decision.take:
        return intent
    reason = decision.reason.strip() or "skipped by filter"
    return _skip(intent, reason)


async def _filter_with_llm(
    prompt: str,
    intent: TradeIntent,
    *,
    db: Session | None = None,
    user_id: str | None = None,
    alert_id: str | None = None,
) -> FilterDecision:
    user_content = (
        "User rules:\n"
        f"{prompt}\n\n"
        "Parsed trade intent (do not change strike, quantity, or side; only decide take or skip):\n"
        f"{json.dumps(_compact_intent(intent))}"
    )
    try:
        completion = await chat_json_completion(
            system=(
                "Follow the user's trading rules and return only JSON. "
                "Set take=true to execute the trade as parsed. "
                "Set take=false to skip it. "
                "Do not invent fills or change strike, quantity, or side."
            ),
            user=user_content,
            output_type=FilterDecision,
            timeout=15,
        )
    except Exception as exc:
        record_ai_evaluation(
            db,
            user_id=user_id,
            alert_id=alert_id,
            kind="filter",
            decision="error",
            rationale=str(exc)[:500],
        )
        raise
    decision = FilterDecision.model_validate(completion.content)
    record_ai_evaluation(
        db,
        user_id=user_id,
        alert_id=alert_id,
        kind="filter",
        decision="take" if decision.take else "skip",
        rationale=decision.reason,
        output=completion.content,
        completion=completion,
    )
    return decision
