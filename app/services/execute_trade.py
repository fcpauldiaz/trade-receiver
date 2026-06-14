import json

from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter, OptionContract
from app.models.tables import InboundAlert, TradeExecution, User
from app.schemas.trade import ValidatedTrade


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

    result = await adapter.place_order(contract, validated.quantity, side, mode)
    status = "filled" if result.success else "failed"
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
        fill_price=float(result.fill_price) if result.fill_price else None,
        intent_json=validated.model_dump_json(),
        broker_response_json=json.dumps(result.raw_response),
    )
    db.add(execution)
    alert.processed = True
    db.commit()
    db.refresh(execution)
    return execution
