import json

from sqlalchemy.orm import Session

from app.brokers.ninjatrader import NinjaTraderAdapter
from app.models.tables import InboundAlert, TradeExecution, User
from app.schemas.trade import ValidatedFuturesTrade


async def execute_futures_trade(
    db: Session,
    user: User,
    alert: InboundAlert,
    validated: ValidatedFuturesTrade,
    adapter: NinjaTraderAdapter,
) -> TradeExecution:
    mode = user.default_mode
    if mode == "live" and not user.live_trading_enabled:
        mode = "paper"

    if validated.validation_errors:
        execution = TradeExecution(
            user_id=user.id,
            alert_id=alert.id,
            broker=validated.broker,
            mode=mode,
            status="validation_failed",
            underlying=validated.symbol,
            option_type="future",
            strike=0.0,
            expiration="",
            quantity=validated.quantity,
            contract_symbol=validated.symbol,
            intent_json=validated.model_dump_json(),
            broker_response_json=json.dumps({"errors": validated.validation_errors}),
        )
        db.add(execution)
        db.commit()
        return execution

    dry_run = mode == "paper"
    result = await adapter.execute_futures_order(validated, mode=mode, dry_run=dry_run)
    status = "submitted" if result.success else "failed"
    if dry_run and result.success:
        status = "submitted"

    execution = TradeExecution(
        user_id=user.id,
        alert_id=alert.id,
        broker=validated.broker,
        mode=mode,
        status=status,
        underlying=validated.symbol,
        option_type="future",
        strike=0.0,
        expiration="",
        quantity=validated.quantity,
        contract_symbol=validated.symbol,
        broker_order_id=result.order_id,
        intent_json=validated.model_dump_json(),
        broker_response_json=json.dumps(result.raw_response or {}),
    )
    db.add(execution)
    alert.processed = True
    db.commit()
    db.refresh(execution)
    return execution
