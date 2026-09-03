import re
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.schemas.trade import TradeIntent
from app.services.ai_evaluations import record_ai_evaluation
from app.services.futures_trade import map_futures_order_payload, parse_futures_alert_rules
from app.services.llm import chat_json_completion, llm_configured

SAMPLE_ALERTS = [
    "BTO SPY 580C 6/20 @ 2.50",
    "STC QQQ 480P 07/18",
    "🚨 BUY TO OPEN AAPL 200 CALL 2025-09-19",
    "BUY MES 1 SL 10 TP 20",
]

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _extract_quantity(text: str) -> int:
    upper = text.upper()
    patterns = [
        r"\b(?:QTY|QUANTITY)\s*[:=]?\s*(\d+)\b",
        r"\b(\d+)\s*(?:CONTRACTS?|CT|LOTS?)\b",
        r"\b[xX]\s*(\d+)\b",
        r"\b(\d+)\s*[xX]\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, upper)
        if match:
            qty = int(match.group(1))
            if qty >= 1:
                return qty
    return 1


def _parse_expiration(token: str) -> date | None:
    token = token.strip().replace("/", "-")
    m = re.match(r"^(\d{1,2})-(\d{1,2})(?:-(\d{2,4}))?$", token)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else date.today().year
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", token)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def parse_alert_rules(text: str) -> TradeIntent:
    futures = parse_futures_alert_rules(text)
    if futures is not None:
        return futures

    upper = text.upper()
    action = "skip"
    if any(k in upper for k in ("BTO", "BUY TO OPEN", "BUY")):
        action = "buy_to_open"
    elif any(k in upper for k in ("STC", "SELL TO CLOSE", "SELL")):
        action = "sell_to_close"

    skip_words = {
        "BTO", "STC", "BUY", "SELL", "TO", "OPEN", "CLOSE", "CALL", "PUT",
        "THE", "AND", "FOR", "AT", "ON", "OR", "A", "AN",
    }
    underlying = ""
    for match in re.finditer(r"\b([A-Z]{1,5})\b", upper):
        token = match.group(1)
        if token not in skip_words:
            underlying = token
            break

    option_type: str = "call"
    if re.search(r"\bP\b|\bPUT\b", upper):
        option_type = "put"
    elif re.search(r"\bC\b|\bCALL\b", upper):
        option_type = "call"

    strike = Decimal("0")
    strike_m = re.search(r"\b(\d+(?:\.\d+)?)\s*[CP]\b", upper)
    if strike_m:
        strike = Decimal(strike_m.group(1))
    else:
        strike_m2 = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:CALL|PUT)\b", upper)
        if strike_m2:
            strike = Decimal(strike_m2.group(1))

    expiration = None
    for token in re.split(r"\s+", text):
        exp = _parse_expiration(token)
        if exp:
            expiration = exp
            break

    quantity = _extract_quantity(text)
    confidence = 0.85 if action != "skip" and underlying and strike > 0 and expiration else 0.3
    return TradeIntent(
        action=action,
        underlying=underlying,
        option_type=option_type,
        strike=strike,
        expiration=expiration,
        quantity=quantity,
        confidence=confidence,
        rationale="rule-based parse",
    )


async def parse_alert(
    text: str,
    body: dict | None = None,
    *,
    db: Session | None = None,
    user_id: str | None = None,
    alert_id: str | None = None,
) -> TradeIntent:
    if body is not None:
        from app.services.futures_trade import is_futures_order_payload

        if is_futures_order_payload(body):
            return map_futures_order_payload(body)

    if llm_configured():
        try:
            return await _parse_with_llm(text, db=db, user_id=user_id, alert_id=alert_id)
        except Exception:
            pass
    return parse_alert_rules(text)


async def _parse_with_llm(
    text: str,
    *,
    db: Session | None = None,
    user_id: str | None = None,
    alert_id: str | None = None,
) -> TradeIntent:
    from app.services.agent_config import get_agent_config, render_user_prompt

    config = get_agent_config(db, "parse")
    prompt = render_user_prompt(config.user_prompt_template, alert_text=text)
    try:
        completion = await chat_json_completion(
            system=config.system_prompt,
            user=prompt,
            output_type=TradeIntent,
            model=config.model,
        )
    except Exception as exc:
        record_ai_evaluation(
            db,
            user_id=user_id,
            alert_id=alert_id,
            kind="parse",
            decision="error",
            rationale=str(exc)[:500],
        )
        raise
    intent = TradeIntent.model_validate(completion.content)
    decision = "skip" if intent.action == "skip" else "take"
    record_ai_evaluation(
        db,
        user_id=user_id,
        alert_id=alert_id,
        kind="parse",
        decision=decision,
        rationale=intent.rationale,
        output=completion.content,
        completion=completion,
    )
    return intent
