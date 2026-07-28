from datetime import date
from decimal import Decimal

from app.brokers.base import BrokerAdapter, OptionContract
from app.schemas.trade import TradeIntent, ValidatedTrade


def _match_exact_contract(
    contracts: list[OptionContract],
    underlying: str,
    option_type: str,
    strike: Decimal,
    expiration: date,
) -> OptionContract | None:
    underlying_u = underlying.upper()
    allowed = {underlying_u}
    if underlying_u == "SPX":
        allowed.add("SPXW")
    for c in contracts:
        if (
            c.underlying.upper() in allowed
            and c.option_type == option_type
            and c.strike == strike
            and c.expiration == expiration
        ):
            return c
    return None


def _closest_contract(
    contracts: list[OptionContract],
    underlying: str,
    option_type: str,
    target_strike: Decimal,
    expiration: date,
) -> OptionContract | None:
    underlying_u = underlying.upper()
    allowed_underlyings = {underlying_u}
    if underlying_u == "SPX":
        allowed_underlyings.add("SPXW")

    candidates = [
        c
        for c in contracts
        if c.underlying.upper() in allowed_underlyings
        and c.option_type == option_type
        and c.expiration == expiration
    ]
    if not candidates:
        # Fall back: any expiration matching type (prefer 0DTE already requested)
        candidates = [
            c
            for c in contracts
            if c.underlying.upper() in allowed_underlyings and c.option_type == option_type
        ]
    if not candidates:
        return None

    # Prefer SPXW symbols when trading SPX index options
    if underlying_u == "SPX":
        spxw = [c for c in candidates if "SPXW" in c.symbol.upper() or c.underlying.upper() == "SPXW"]
        if spxw:
            candidates = spxw

    return min(candidates, key=lambda c: abs(c.strike - target_strike))


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
    contract = _match_exact_contract(
        contracts, intent.underlying, intent.option_type, intent.strike, exp
    )
    if contract is None and intent.strike > 0:
        contract = _closest_contract(
            contracts, intent.underlying, intent.option_type, intent.strike, exp
        )
    if contract is None:
        errors.append("contract not found in option chain")

    bid, ask, oi = None, None, None
    contract_symbol = ""
    strike = intent.strike
    expiration = exp
    if contract:
        bid, ask, oi = contract.bid, contract.ask, contract.open_interest
        contract_symbol = contract.symbol
        strike = contract.strike
        expiration = contract.expiration
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
        strike=strike,
        expiration=expiration,
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
        take_profit_pct=intent.take_profit_pct,
        source=intent.source,
        notional_usd=intent.notional_usd,
    )
