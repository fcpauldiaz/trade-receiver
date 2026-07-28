import json
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter, OptionContract
from app.models.tables import InboundAlert, TradeExecution, User
from app.schemas.trade import ValidatedTrade
from app.services.order_lifecycle import compute_close_pnl, resolve_fill
from app.services.position_check import validate_close_position


def _take_profit_price(validated: ValidatedTrade) -> Decimal | None:
    if validated.take_profit_pct is None or validated.take_profit_pct <= 0:
        return None
    entry = validated.ask or validated.limit_price or validated.bid
    if entry is None or entry <= 0:
        return None
    tp = entry * (Decimal("1") + validated.take_profit_pct)
    return tp.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def execute_trade(
    db: Session,
    user: User,
    alert: InboundAlert,
    validated: ValidatedTrade,
    adapter: BrokerAdapter,
) -> TradeExecution:
    mode = user.default_mode
    if mode == "live" and not user.live_trading_enabled:
        mode = "paper"

    side = "buy_to_open" if validated.action == "buy_to_open" else "sell_to_close"
    contract = OptionContract(
        symbol=validated.contract_symbol,
        underlying=validated.underlying,
        option_type=validated.option_type,
        strike=validated.strike,
        expiration=validated.expiration,
        bid=validated.bid,
        ask=validated.ask,
        open_interest=validated.open_interest,
    )

    if validated.validation_errors:
        execution = TradeExecution(
            user_id=user.id,
            alert_id=alert.id,
            broker=validated.broker,
            mode=mode,
            status="validation_failed",
            underlying=validated.underlying,
            option_type=validated.option_type,
            strike=float(validated.strike),
            expiration=validated.expiration.isoformat(),
            quantity=validated.quantity,
            contract_symbol=validated.contract_symbol,
            intent_json=validated.model_dump_json(),
            broker_response_json=json.dumps({"errors": validated.validation_errors}),
        )
        db.add(execution)
        db.commit()
        return execution

    if validated.action == "sell_to_close":
        pos_error = await validate_close_position(adapter, validated.contract_symbol, validated.quantity)
        if pos_error:
            execution = TradeExecution(
                user_id=user.id,
                alert_id=alert.id,
                broker=validated.broker,
                mode=mode,
                status="skipped",
                underlying=validated.underlying,
                option_type=validated.option_type,
                strike=float(validated.strike),
                expiration=validated.expiration.isoformat(),
                quantity=validated.quantity,
                contract_symbol=validated.contract_symbol,
                intent_json=validated.model_dump_json(),
                broker_response_json=json.dumps({"error": pos_error}),
            )
            db.add(execution)
            alert.processed = True
            alert.skip_reason = pos_error
            db.commit()
            return execution

    tp_price = _take_profit_price(validated) if validated.action == "buy_to_open" else None
    if tp_price is not None:
        result = await adapter.place_order_with_take_profit(
            contract,
            validated.quantity,
            side,
            mode,
            take_profit_price=tp_price,
        )
    else:
        result = await adapter.place_order(contract, validated.quantity, side, mode)

    status, fill_price = await resolve_fill(adapter, result.order_id, validated.ask)
    if not result.success:
        status = "failed"

    pnl = None
    if status == "filled" and validated.action == "sell_to_close":
        pnl = compute_close_pnl(db, user.id, validated.contract_symbol, validated.quantity, fill_price)

    broker_payload = dict(result.raw_response or {})
    if tp_price is not None:
        broker_payload.setdefault("take_profit_price", float(tp_price))
        if validated.take_profit_pct is not None:
            broker_payload.setdefault("take_profit_pct", float(validated.take_profit_pct))

    execution = TradeExecution(
        user_id=user.id,
        alert_id=alert.id,
        broker=validated.broker,
        mode=mode,
        status=status,
        underlying=validated.underlying,
        option_type=validated.option_type,
        strike=float(validated.strike),
        expiration=validated.expiration.isoformat(),
        quantity=validated.quantity,
        contract_symbol=validated.contract_symbol,
        fill_price=fill_price,
        pnl=pnl,
        broker_order_id=result.order_id,
        intent_json=validated.model_dump_json(),
        broker_response_json=json.dumps(broker_payload),
    )
    db.add(execution)
    alert.processed = True
    db.commit()
    db.refresh(execution)
    return execution
