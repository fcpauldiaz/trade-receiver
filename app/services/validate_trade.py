from datetime import date
from decimal import Decimal

from app.brokers.base import BrokerAdapter, OptionContract
from app.schemas.trade import TradeIntent, ValidatedTrade


def _match_contract(
    contracts: list[OptionContract],
    underlying: str,
    option_type: str,
    strike: Decimal,
    expiration: date,
) -> OptionContract | None:
    for c in contracts:
        if (
            c.underlying == underlying.upper()
            and c.option_type == option_type
            and c.strike == strike
            and c.expiration == expiration
        ):
            return c
    return None


async def validate_trade(
    intent: TradeIntent,
    broker_name: str,
    adapter: BrokerAdapter,
    *,
    min_open_interest: int = 0,
    max_spread_pct: float = 50.0,
) -> ValidatedTrade:
    errors: list[str] = []
    exp = intent.expiration or date.today()
    if intent.expiration is None:
        errors.append("missing expiration")
    if intent.strike <= 0:
        errors.append("invalid strike")
    if not intent.underlying:
        errors.append("missing underlying")

    contracts = await adapter.get_option_chain(intent.underlying, exp)
    contract = _match_contract(contracts, intent.underlying, intent.option_type, intent.strike, exp)
    if contract is None:
        errors.append("contract not found in option chain")

    bid, ask, oi = None, None, None
    contract_symbol = ""
    if contract:
        bid, ask, oi = contract.bid, contract.ask, contract.open_interest
        contract_symbol = contract.symbol
        if oi is not None and oi < min_open_interest:
            errors.append(f"open interest {oi} below minimum {min_open_interest}")
        if bid and ask and bid > 0:
            spread_pct = float((ask - bid) / bid * 100)
            if spread_pct > max_spread_pct:
                errors.append(f"spread {spread_pct:.1f}% exceeds max {max_spread_pct}%")

    return ValidatedTrade(
        action=intent.action,
        underlying=intent.underlying.upper(),
        option_type=intent.option_type,
        strike=intent.strike,
        expiration=exp,
        quantity=intent.quantity,
        order_type=intent.order_type,
        limit_price=intent.limit_price,
        confidence=intent.confidence,
        rationale=intent.rationale,
        broker=broker_name,  # type: ignore[arg-type]
        contract_symbol=contract_symbol,
        bid=bid,
        ask=ask,
        open_interest=oi,
        validation_errors=errors,
    )
