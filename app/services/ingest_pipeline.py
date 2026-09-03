import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.decide_action import decide_action
from app.agents.filter_trade import apply_trade_filter
from app.agents.parse_alert import parse_alert
from app.brokers.ninjatrader import NinjaTraderAdapter
from app.models.tables import BrokerConnection, InboundAlert, User
from app.services.compute_quantity import compute_quantity
from app.services.entitlements import can_process_trades, require_active_subscription
from app.services.eterminal_signal import (
    eterminal_idempotency_key,
    is_eterminal_envelope,
    map_eterminal_signal,
)
from app.services.execute_futures_trade import execute_futures_trade
from app.services.execute_trade import execute_trade
from app.services.futures_trade import intent_to_validated_futures, is_futures_order_payload
from app.services import market_hours
from app.services.option_chain import get_adapter
from app.services.validate_trade import validate_trade
from app.services.webhook_normalize import idempotency_key, normalize_webhook_body


def resolve_idempotency_key(user: User, body: dict, webhook_id: str | None = None) -> str:
    if is_eterminal_envelope(body):
        return eterminal_idempotency_key(user.id, body)
    if is_futures_order_payload(body):
        import hashlib

        raw = json.dumps(
            {"user_id": user.id, "webhook_id": webhook_id, "body": body},
            sort_keys=True,
            default=str,
        )
        key = hashlib.sha256(raw.encode()).hexdigest()
        if webhook_id:
            return f"{webhook_id}:{key}"
        return key
    _, payload = normalize_webhook_body(body)
    key = idempotency_key(user.id, payload)
    if webhook_id:
        return f"{webhook_id}:{key}"
    return key


def duplicate_response(db: Session, user: User, body: dict, webhook_id: str | None = None) -> dict:
    key = resolve_idempotency_key(user, body, webhook_id)
    existing = db.query(InboundAlert).filter_by(user_id=user.id, idempotency_key=key).first()
    if existing is not None:
        return {"status": "duplicate", "alert_id": existing.id}
    raise RuntimeError("ingest integrity error without matching alert row")


def resolve_broker_connection(db: Session, user: User) -> BrokerConnection | None:
    if user.default_broker:
        conn = db.query(BrokerConnection).filter_by(
            user_id=user.id, broker=user.default_broker, status="connected"
        ).first()
        if conn:
            return conn
    return db.query(BrokerConnection).filter_by(user_id=user.id, status="connected").first()


def _compute_futures_quantity(user: User, validated) -> tuple[int, str | None]:
    qty = max(1, min(validated.quantity, user.max_contracts))
    return qty, None


async def process_inbound_alert(
    db: Session,
    user: User,
    body: dict,
    *,
    webhook_id: str | None = None,
) -> dict:
    text, payload = normalize_webhook_body(body)
    key = resolve_idempotency_key(user, body, webhook_id)

    existing = db.query(InboundAlert).filter_by(user_id=user.id, idempotency_key=key).first()
    if existing:
        return {"status": "duplicate", "alert_id": existing.id}

    active = can_process_trades(user)
    alert = InboundAlert(
        user_id=user.id,
        inbound_webhook_id=webhook_id,
        idempotency_key=key,
        raw_payload=json.dumps(body),
        normalized_text=text,
        subscription_active=active,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)

    if not active:
        ok, reason = require_active_subscription(user)
        alert.skip_reason = reason
        alert.processed = True
        db.commit()
        raise HTTPException(status_code=402, detail=reason)

    if is_eterminal_envelope(body):
        intent = map_eterminal_signal(body)
        if intent is None:
            alert.skip_reason = "eterminal event not a tradable signal"
            alert.processed = True
            db.commit()
            return {"status": "skipped", "reason": alert.skip_reason}
    else:
        intent = await parse_alert(text, body=body)
        intent = decide_action(intent, user)
        if intent.action != "skip":
            intent = await apply_trade_filter(intent, user)
        if intent.action == "skip":
            alert.skip_reason = intent.rationale or "skipped"
            alert.processed = True
            db.commit()
            return {"status": "skipped", "reason": alert.skip_reason}

    if intent.action == "skip":
        alert.skip_reason = intent.rationale or "skipped"
        alert.processed = True
        db.commit()
        return {"status": "skipped", "reason": alert.skip_reason}

    if not market_hours.is_rth():
        alert.skip_reason = market_hours.RTH_SKIP_REASON
        alert.processed = True
        db.commit()
        return {"status": "skipped", "reason": alert.skip_reason}

    connection = resolve_broker_connection(db, user)
    if connection is None:
        alert.skip_reason = "no broker connected"
        alert.processed = True
        db.commit()
        return {"status": "skipped", "reason": alert.skip_reason}

    if intent.asset_class == "future":
        if connection.broker != "ninjatrader":
            alert.skip_reason = "futures trading requires NinjaTrader connection"
            alert.processed = True
            db.commit()
            return {"status": "skipped", "reason": alert.skip_reason}

        adapter = await get_adapter(db, connection)
        if not isinstance(adapter, NinjaTraderAdapter):
            alert.skip_reason = "invalid NinjaTrader adapter"
            alert.processed = True
            db.commit()
            return {"status": "skipped", "reason": alert.skip_reason}

        validated_futures = intent_to_validated_futures(intent, connection.broker)
        quantity, sizing_skip = _compute_futures_quantity(user, validated_futures)
        if sizing_skip:
            alert.skip_reason = sizing_skip
            alert.processed = True
            db.commit()
            return {"status": "skipped", "reason": sizing_skip}
        validated_futures = validated_futures.model_copy(update={"quantity": quantity})
        execution = await execute_futures_trade(db, user, alert, validated_futures, adapter)
        return {
            "status": execution.status,
            "trade_id": execution.id,
            "validation_errors": validated_futures.validation_errors,
        }

    if connection.broker == "ninjatrader":
        alert.skip_reason = "NinjaTrader only supports futures orders"
        alert.processed = True
        db.commit()
        return {"status": "skipped", "reason": alert.skip_reason}

    adapter = await get_adapter(db, connection)
    validated = await validate_trade(intent, connection.broker, adapter)

    quantity, sizing_skip = await compute_quantity(user, validated, adapter)
    if sizing_skip:
        alert.skip_reason = sizing_skip
        alert.processed = True
        db.commit()
        return {"status": "skipped", "reason": sizing_skip}

    validated = validated.model_copy(update={"quantity": quantity})
    execution = await execute_trade(db, user, alert, validated, adapter)
    return {
        "status": execution.status,
        "trade_id": execution.id,
        "validation_errors": validated.validation_errors,
    }
