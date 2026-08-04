import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.agents.decide_action import decide_action
from app.agents.parse_alert import parse_alert
from app.api.deps import get_current_user
from app.database import get_db
from app.models.tables import BrokerConnection, InboundAlert, User
from app.services.compute_quantity import compute_quantity
from app.services.entitlements import can_process_trades, require_active_subscription
from app.services.eterminal_signal import (
    eterminal_idempotency_key,
    is_eterminal_envelope,
    map_eterminal_signal,
)
from app.services import market_hours
from app.services.execute_trade import execute_trade
from app.services.option_chain import get_adapter
from app.services.validate_trade import validate_trade
from app.services.webhook_normalize import idempotency_key, normalize_webhook_body

router = APIRouter(tags=["ingest"])


def _resolve_broker_connection(db: Session, user: User) -> BrokerConnection | None:
    if user.default_broker:
        conn = db.query(BrokerConnection).filter_by(
            user_id=user.id, broker=user.default_broker, status="connected"
        ).first()
        if conn:
            return conn
    return db.query(BrokerConnection).filter_by(user_id=user.id, status="connected").first()


async def _process_inbound_alert(db: Session, user: User, body: dict) -> dict:
    text, payload = normalize_webhook_body(body)
    if is_eterminal_envelope(body):
        key = eterminal_idempotency_key(user.id, body)
    else:
        key = idempotency_key(user.id, payload)

    existing = db.query(InboundAlert).filter_by(user_id=user.id, idempotency_key=key).first()
    if existing:
        return {"status": "duplicate", "alert_id": existing.id}

    active = can_process_trades(user)
    alert = InboundAlert(
        user_id=user.id,
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
        intent = await parse_alert(text)
        intent = decide_action(intent, user)
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

    connection = _resolve_broker_connection(db, user)
    if connection is None:
        alert.skip_reason = "no broker connected"
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


@router.post("/v1/ingest")
async def ingest_alert(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not can_process_trades(user):
        _, reason = require_active_subscription(user)
        raise HTTPException(status_code=402, detail=reason)
    body = await request.json()
    return await _process_inbound_alert(db, user, body)
