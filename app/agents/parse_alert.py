import json
import re
from datetime import date
from decimal import Decimal

from app.config import settings
from app.schemas.trade import TradeIntent
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


async def parse_alert(text: str, body: dict | None = None) -> TradeIntent:
    if body is not None:
        from app.services.futures_trade import is_futures_order_payload

        if is_futures_order_payload(body):
            return map_futures_order_payload(body)

    if llm_configured():
        try:
            return await _parse_with_llm(text)
        except Exception:
            pass
    return parse_alert_rules(text)


async def _parse_with_llm(text: str) -> TradeIntent:
    schema = TradeIntent.model_json_schema()
    prompt = (
        "Parse this trade alert into structured JSON. "
        "Use asset_class=future for futures symbols like ES, MES, NQ, MNQ, YM, RTY with BUY/SELL. "
        "Use asset_class=option for options alerts with strikes and expirations. "
        "Use action skip if not a trade alert. "
        "Include quantity; default to 1 if not specified. "
        "For futures, map BUY to buy_to_open and SELL to sell_to_close.\n\n"
        + text
    )
    content = await chat_json_completion(
        system="Return only valid JSON matching the trade intent schema.",
        user=prompt,
        schema=schema,
    )
    return TradeIntent.model_validate(content)
