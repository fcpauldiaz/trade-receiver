import math
from decimal import Decimal

from app.brokers.base import BrokerAdapter
from app.models.tables import User
from app.schemas.trade import ValidatedTrade

def _cap_quantity(quantity: int, max_contracts: int) -> int:
    return max(1, min(quantity, max_contracts))


async def compute_quantity(
    user: User,
    validated: ValidatedTrade,
    adapter: BrokerAdapter,
) -> tuple[int, str | None]:
    mode = user.sizing_mode or "alert_inferred"

    if mode == "fixed":
        return _cap_quantity(user.fixed_contracts, user.max_contracts), None

    if mode == "alert_inferred":
        return _cap_quantity(max(1, validated.quantity), user.max_contracts), None

    if mode == "risk_percent":
        equity = await adapter.get_account_equity()
        if equity is None or equity <= 0:
            return 0, "unable to fetch account equity for risk-based sizing"

        price = validated.ask or validated.limit_price
        if price is None or price <= 0:
            return 0, "missing option price for risk-based sizing"

        risk_dollars = equity * Decimal(str(user.risk_percent)) / Decimal("100")
        cost_per_contract = price * Decimal("100")
        if cost_per_contract <= 0:
            return 0, "invalid contract cost for risk-based sizing"

        raw_qty = int(math.floor(float(risk_dollars / cost_per_contract)))
        quantity = _cap_quantity(max(1, raw_qty), user.max_contracts)
        return quantity, None

    return _cap_quantity(max(1, validated.quantity), user.max_contracts), None
