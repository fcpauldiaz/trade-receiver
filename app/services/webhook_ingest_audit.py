from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import WebhookIngestEvent

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


def record_webhook_ingest_event(
    db: Session,
    *,
    user_id: str,
    inbound_webhook_id: str,
    body: dict[str, Any],
    result: dict[str, Any],
) -> WebhookIngestEvent:
    status = str(result.get("status") or "unknown")
    detail = result.get("reason") or result.get("detail")
    if detail is not None:
        detail = str(detail)[:512]

    event = WebhookIngestEvent(
        user_id=user_id,
        inbound_webhook_id=inbound_webhook_id,
        status=status,
        request_payload=serialize_webhook_payload(body),
        alert_id=result.get("alert_id"),
        trade_id=result.get("trade_id"),
        detail=detail,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
