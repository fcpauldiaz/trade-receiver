import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.agents.decide_action import decide_action
from app.agents.parse_alert import parse_alert
from app.database import get_db
from app.models.tables import BrokerConnection, InboundAlert, User
from app.services.entitlements import can_process_trades, require_active_subscription
from app.services.execute_trade import execute_trade
from app.services.option_chain import get_adapter
from app.services.validate_trade import validate_trade
from app.services.webhook_normalize import idempotency_key, normalize_webhook_body

router = APIRouter(tags=["webhooks"])


def _get_user(db: Session, user_id: str, secret: str) -> User:
    user = db.get(User, user_id)
    if user is None or not user.webhook_secret or not secrets_compare(user.webhook_secret, secret):
        raise HTTPException(status_code=401, detail="Invalid webhook credentials")
    if not user.webhook_enabled:
        raise HTTPException(status_code=402, detail="Webhook disabled — subscription required")
    return user


def secrets_compare(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a, b)


@router.post("/hooks/{user_id}/{secret}")
async def receive_webhook(user_id: str, secret: str, request: Request, db: Session = Depends(get_db)):
    user = _get_user(db, user_id, secret)
    body = await request.json()
    text, payload = normalize_webhook_body(body)
    key = idempotency_key(user_id, payload)
    existing = db.query(InboundAlert).filter_by(user_id=user_id, idempotency_key=key).first()
    if existing:
        return {"status": "duplicate", "alert_id": existing.id}

    active = can_process_trades(user)
    alert = InboundAlert(
        user_id=user_id,
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

    intent = await parse_alert(text)
    intent = decide_action(intent, user)
    if intent.action == "skip":
        alert.skip_reason = intent.rationale or "skipped"
        alert.processed = True
        db.commit()
        return {"status": "skipped", "reason": alert.skip_reason}

    connection = (
        db.query(BrokerConnection)
        .filter_by(user_id=user_id, status="connected")
        .first()
    )
    if connection is None:
        alert.skip_reason = "no broker connected"
        alert.processed = True
        db.commit()
        return {"status": "skipped", "reason": alert.skip_reason}

    adapter = get_adapter(connection)
    validated = await validate_trade(intent, connection.broker, adapter)
    execution = await execute_trade(db, user, alert, validated, adapter)
    return {
        "status": execution.status,
        "trade_id": execution.id,
        "validation_errors": validated.validation_errors,
    }
