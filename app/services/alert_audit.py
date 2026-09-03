from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.models.tables import InboundAlert, InboundWebhook, TradeExecution, User, WebhookIngestEvent
from app.services.webhook_ingest_audit import parse_stored_payload
from app.services.webhook_normalize import normalize_webhook_body

AlertOutcome = Literal["executed", "skipped", "pending"]
AlertSource = Literal["ingest", "webhook"]


class AlertAuditItem(BaseModel):
    id: str
    created_at: datetime
    source: AlertSource = "ingest"
    webhook_id: str | None = None
    webhook_name: str | None = None
    source_app: str
    platform: str
    title: str
    text: str
    outcome: AlertOutcome
    ingest_status: str | None = None
    skip_reason: str | None
    alert_id: str | None = None
    trade_id: str | None
    trade_status: str | None
    payload: dict[str, Any] | list[Any] | str | None = None
    user_id: str | None = None
    user_email: str | None = None


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


def _is_take_profit_leg(trade: TradeExecution) -> bool:
    if not trade.broker_response_json:
        return False
    try:
        payload = json.loads(trade.broker_response_json)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("role") == "take_profit"


def alert_outcome(alert: InboundAlert, trade: TradeExecution | None) -> AlertOutcome:
    if alert.skip_reason or (trade is not None and trade.status == "skipped"):
        return "skipped"
    if trade is not None:
        return "executed"
    return "pending"


def _webhook_event_outcome(event: WebhookIngestEvent, alert: InboundAlert | None, trade: TradeExecution | None) -> AlertOutcome:
    if event.status in {"duplicate", "subscription_inactive"}:
        return "skipped"
    if alert is not None:
        return alert_outcome(alert, trade)
    if event.status in {"skipped", "validation_failed"}:
        return "skipped"
    if trade is not None:
        return "executed"
    if event.status in {"filled", "submitted"}:
        return "executed"
    return "pending"


def _trade_by_alert(db: Session, alert_ids: list[str]) -> dict[str, TradeExecution]:
    if not alert_ids:
        return {}
    trades = db.query(TradeExecution).filter(TradeExecution.alert_id.in_(alert_ids)).all()
    trade_by_alert: dict[str, TradeExecution] = {}
    for trade in trades:
        if not trade.alert_id:
            continue
        existing = trade_by_alert.get(trade.alert_id)
        if existing is None:
            trade_by_alert[trade.alert_id] = trade
        elif _is_take_profit_leg(existing) and not _is_take_profit_leg(trade):
            trade_by_alert[trade.alert_id] = trade
    return trade_by_alert


def _alert_audit_from_ingest(
    row: InboundAlert,
    trade: TradeExecution | None,
) -> AlertAuditItem:
    source_app, platform, title, text = payload_preview(row.raw_payload)
    return AlertAuditItem(
        id=row.id,
        created_at=row.created_at,
        source="ingest",
        source_app=source_app,
        platform=platform,
        title=title,
        text=text or row.normalized_text,
        outcome=alert_outcome(row, trade),
        skip_reason=row.skip_reason,
        alert_id=row.id,
        trade_id=trade.id if trade else None,
        trade_status=trade.status if trade else None,
        payload=parse_stored_payload(row.raw_payload),
    )


def _alert_audit_from_webhook_event(
    event: WebhookIngestEvent,
    webhook: InboundWebhook,
    alert: InboundAlert | None,
    trade: TradeExecution | None,
) -> AlertAuditItem:
    source_app, platform, title, text = payload_preview(event.request_payload)
    normalized_text = alert.normalized_text if alert is not None else ""
    return AlertAuditItem(
        id=event.id,
        created_at=event.created_at,
        source="webhook",
        webhook_id=webhook.id,
        webhook_name=webhook.name,
        source_app=source_app,
        platform=platform,
        title=title,
        text=text or normalized_text,
        outcome=_webhook_event_outcome(event, alert, trade),
        ingest_status=event.status,
        skip_reason=event.detail or (alert.skip_reason if alert is not None else None),
        alert_id=event.alert_id,
        trade_id=event.trade_id or (trade.id if trade else None),
        trade_status=trade.status if trade else None,
        payload=parse_stored_payload(event.request_payload),
    )


def list_alert_audit(db: Session, user_id: str, limit: int = 100) -> list[AlertAuditItem]:
    ingest_rows = (
        db.query(InboundAlert)
        .filter(InboundAlert.user_id == user_id, InboundAlert.inbound_webhook_id.is_(None))
        .order_by(InboundAlert.created_at.desc())
        .limit(limit)
        .all()
    )
    webhook_events = (
        db.query(WebhookIngestEvent, InboundWebhook)
        .join(InboundWebhook, WebhookIngestEvent.inbound_webhook_id == InboundWebhook.id)
        .filter(WebhookIngestEvent.user_id == user_id)
        .order_by(WebhookIngestEvent.created_at.desc())
        .limit(limit)
        .all()
    )

    alert_ids = [row.id for row in ingest_rows]
    alert_ids.extend(event.alert_id for event, _ in webhook_events if event.alert_id)
    trade_by_alert = _trade_by_alert(db, alert_ids)

    alerts_by_id: dict[str, InboundAlert] = {}
    if alert_ids:
        linked_alerts = db.query(InboundAlert).filter(InboundAlert.id.in_(alert_ids)).all()
        alerts_by_id = {row.id: row for row in linked_alerts}

    items: list[AlertAuditItem] = []
    for row in ingest_rows:
        items.append(_alert_audit_from_ingest(row, trade_by_alert.get(row.id)))

    for event, webhook in webhook_events:
        alert = alerts_by_id.get(event.alert_id) if event.alert_id else None
        trade = trade_by_alert.get(event.alert_id) if event.alert_id else None
        items.append(_alert_audit_from_webhook_event(event, webhook, alert, trade))

    items.sort(key=lambda item: item.created_at, reverse=True)
    return items[:limit]


def list_alert_audit_admin(
    db: Session,
    *,
    limit: int = 100,
    email: str | None = None,
    outcome: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> list[AlertAuditItem]:
    ingest_q = db.query(InboundAlert, User).join(User, InboundAlert.user_id == User.id).filter(
        InboundAlert.inbound_webhook_id.is_(None)
    )
    webhook_q = (
        db.query(WebhookIngestEvent, InboundWebhook, User)
        .join(InboundWebhook, WebhookIngestEvent.inbound_webhook_id == InboundWebhook.id)
        .join(User, WebhookIngestEvent.user_id == User.id)
    )
    if email:
        like = f"%{email.strip()}%"
        ingest_q = ingest_q.filter(User.email.ilike(like))
        webhook_q = webhook_q.filter(User.email.ilike(like))
    if from_dt is not None:
        ingest_q = ingest_q.filter(InboundAlert.created_at >= from_dt)
        webhook_q = webhook_q.filter(WebhookIngestEvent.created_at >= from_dt)
    if to_dt is not None:
        ingest_q = ingest_q.filter(InboundAlert.created_at <= to_dt)
        webhook_q = webhook_q.filter(WebhookIngestEvent.created_at <= to_dt)

    ingest_rows = ingest_q.order_by(InboundAlert.created_at.desc()).limit(limit).all()
    webhook_events = webhook_q.order_by(WebhookIngestEvent.created_at.desc()).limit(limit).all()

    alert_ids = [row.id for row, _ in ingest_rows]
    alert_ids.extend(event.alert_id for event, _, _ in webhook_events if event.alert_id)
    trade_by_alert = _trade_by_alert(db, alert_ids)

    alerts_by_id: dict[str, InboundAlert] = {}
    if alert_ids:
        linked_alerts = db.query(InboundAlert).filter(InboundAlert.id.in_(alert_ids)).all()
        alerts_by_id = {row.id: row for row in linked_alerts}

    items: list[AlertAuditItem] = []
    for row, user in ingest_rows:
        item = _alert_audit_from_ingest(row, trade_by_alert.get(row.id))
        item.user_id = user.id
        item.user_email = user.email
        items.append(item)

    for event, webhook, user in webhook_events:
        alert = alerts_by_id.get(event.alert_id) if event.alert_id else None
        trade = trade_by_alert.get(event.alert_id) if event.alert_id else None
        item = _alert_audit_from_webhook_event(event, webhook, alert, trade)
        item.user_id = user.id
        item.user_email = user.email
        items.append(item)

    if outcome:
        items = [item for item in items if item.outcome == outcome]

    items.sort(key=lambda item: item.created_at, reverse=True)
    return items[:limit]
