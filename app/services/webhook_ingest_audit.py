from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.tables import InboundAlert, TradeExecution, WebhookIngestEvent

logger = logging.getLogger(__name__)

# Stored request JSON is capped at 256 KiB UTF-8 bytes (see README).
MAX_WEBHOOK_INGEST_PAYLOAD_BYTES = 262_144


def serialize_webhook_payload(body: dict[str, Any]) -> str:
    raw = json.dumps(body, separators=(",", ":"))
    encoded = raw.encode("utf-8")
    if len(encoded) <= MAX_WEBHOOK_INGEST_PAYLOAD_BYTES:
        return raw
    partial = encoded[:MAX_WEBHOOK_INGEST_PAYLOAD_BYTES].decode("utf-8", errors="ignore")
    return json.dumps(
        {
            "_truncated": True,
            "_max_bytes": MAX_WEBHOOK_INGEST_PAYLOAD_BYTES,
            "partial": partial,
        },
        separators=(",", ":"),
    )


def parse_stored_payload(raw_payload: str) -> dict[str, Any] | list[Any] | str:
    try:
        parsed = json.loads(raw_payload)
    except (json.JSONDecodeError, TypeError):
        return raw_payload
    if isinstance(parsed, (dict, list)):
        return parsed
    return raw_payload


def _sanitize_audit_ids(db: Session, result: dict[str, Any]) -> tuple[str | None, str | None]:
    alert_id = result.get("alert_id")
    if alert_id is not None and db.get(InboundAlert, alert_id) is None:
        alert_id = None

    trade_id = result.get("trade_id")
    if trade_id is not None and db.get(TradeExecution, trade_id) is None:
        trade_id = None

    return alert_id, trade_id


def record_webhook_ingest_event(
    db: Session,
    *,
    user_id: str,
    inbound_webhook_id: str,
    body: dict[str, Any],
    result: dict[str, Any],
) -> WebhookIngestEvent:
    status = str(result.get("status") or "unknown")[:32]
    detail = result.get("reason") or result.get("detail")
    if detail is not None:
        detail = str(detail)[:512]

    alert_id, trade_id = _sanitize_audit_ids(db, result)

    event = WebhookIngestEvent(
        user_id=user_id,
        inbound_webhook_id=inbound_webhook_id,
        status=status,
        request_payload=serialize_webhook_payload(body),
        alert_id=alert_id,
        trade_id=trade_id,
        detail=detail,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def safe_record_webhook_ingest_event(
    db: Session,
    *,
    user_id: str,
    inbound_webhook_id: str,
    body: dict[str, Any],
    result: dict[str, Any],
) -> WebhookIngestEvent | None:
    try:
        return record_webhook_ingest_event(
            db,
            user_id=user_id,
            inbound_webhook_id=inbound_webhook_id,
            body=body,
            result=result,
        )
    except SQLAlchemyError:
        logger.exception(
            "webhook ingest audit insert failed webhook_id=%s user_id=%s status=%s",
            inbound_webhook_id,
            user_id,
            result.get("status"),
        )
        db.rollback()
        return None
