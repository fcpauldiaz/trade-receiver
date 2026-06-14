from app.config import settings
from app.models.tables import User
from app.schemas.trade import TradeIntent


def decide_action(intent: TradeIntent, user: User) -> TradeIntent:
    if intent.action == "skip":
        return intent
    if intent.confidence < settings.ai_confidence_threshold:
        return intent.model_copy(update={"action": "skip", "rationale": "confidence below threshold"})
    if user.allowed_tickers:
        allowed = {t.strip().upper() for t in user.allowed_tickers.split(",") if t.strip()}
        if intent.underlying.upper() not in allowed:
            return intent.model_copy(update={"action": "skip", "rationale": "ticker not allowed"})
    if intent.quantity > user.max_contracts:
        intent = intent.model_copy(update={"quantity": user.max_contracts})
    return intent
