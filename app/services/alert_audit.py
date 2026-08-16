from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.models.tables import InboundAlert, TradeExecution
from app.services.webhook_normalize import normalize_webhook_body

AlertOutcome = Literal["executed", "skipped", "pending"]


class AlertAuditItem(BaseModel):
    id: str
    created_at: datetime
    source_app: str
    platform: str
    title: str
    text: str
    outcome: AlertOutcome
    skip_reason: str | None
    trade_id: str | None
    trade_status: str | None


def payload_preview(raw_payload: str) -> tuple[str, str, str, str]:
    try:
        body = json.loads(raw_payload)
    except (json.JSONDecodeError, TypeError):
        return "", "", "", raw_payload
    if not isinstance(body, dict):
        return "", "", "", raw_payload
    try:
        text, payload = normalize_webhook_body(body)
    except ValidationError:
        return (
            str(body.get("app_id") or ""),
            str(body.get("platform") or ""),
            str(body.get("title") or ""),
            str(body.get("body") or raw_payload),
        )
    return payload.app_id, payload.platform, payload.title, text


def alert_outcome(alert: InboundAlert, trade: TradeExecution | None) -> AlertOutcome:
    if trade is not None:
        return "executed"
    if alert.skip_reason:
        return "skipped"
    return "pending"


def list_alert_audit(db: Session, user_id: str, limit: int = 100) -> list[AlertAuditItem]:
    rows = (
        db.query(InboundAlert)
        .filter(InboundAlert.user_id == user_id)
        .order_by(InboundAlert.created_at.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return []
    ids = [row.id for row in rows]
    trades = (
        db.query(TradeExecution)
        .filter(TradeExecution.alert_id.in_(ids))
        .all()
    )
    trade_by_alert = {trade.alert_id: trade for trade in trades if trade.alert_id}
    items: list[AlertAuditItem] = []
    for row in rows:
        trade = trade_by_alert.get(row.id)
        source_app, platform, title, text = payload_preview(row.raw_payload)
        items.append(
            AlertAuditItem(
                id=row.id,
                created_at=row.created_at,
                source_app=source_app,
                platform=platform,
                title=title,
                text=text or row.normalized_text,
                outcome=alert_outcome(row, trade),
                skip_reason=row.skip_reason,
                trade_id=trade.id if trade else None,
                trade_status=trade.status if trade else None,
            )
        )
    return items
