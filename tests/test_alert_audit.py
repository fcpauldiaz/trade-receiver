import json

from app.models.tables import InboundAlert, TradeExecution
from app.services.alert_audit import alert_outcome, payload_preview
from app.services.webhook_normalize import normalize_webhook_body


def test_payload_preview_desktop_notification():
    raw = json.dumps(
        {
            "app_id": "com.hnc.Discord",
            "title": "Alerts",
            "subtitle": "",
            "body": "BTO SPY 580C 6/20 @ 2.50",
            "platform": "macos",
        }
    )
    app_id, platform, title, text = payload_preview(raw)
    assert app_id == "com.hnc.Discord"
    assert platform == "macos"
    assert title == "Alerts"
    assert "BTO SPY" in text


def test_payload_preview_invalid_json():
    app_id, platform, title, text = payload_preview("not-json")
    assert app_id == ""
    assert platform == ""
    assert title == ""
    assert text == "not-json"


def test_alert_outcome_executed_when_trade_exists():
    alert = InboundAlert(user_id="u", idempotency_key="k", raw_payload="{}", normalized_text="x")
    trade = TradeExecution(
        user_id="u",
        broker="tradier",
        mode="paper",
        status="filled",
        underlying="SPY",
        option_type="call",
        strike=580,
        expiration="2026-06-20",
        quantity=1,
    )
    assert alert_outcome(alert, trade) == "executed"


def test_alert_outcome_skipped_when_reason():
    alert = InboundAlert(
        user_id="u",
        idempotency_key="k",
        raw_payload="{}",
        normalized_text="x",
        skip_reason="no broker connected",
        processed=True,
    )
    assert alert_outcome(alert, None) == "skipped"


def test_alert_outcome_pending_when_unprocessed():
    alert = InboundAlert(user_id="u", idempotency_key="k", raw_payload="{}", normalized_text="x")
    assert alert_outcome(alert, None) == "pending"


def test_normalize_round_trip_matches_preview():
    body = {"app_id": "discord", "title": "T", "body": "BTO", "platform": "macos"}
    text, payload = normalize_webhook_body(body)
    preview = payload_preview(json.dumps(body))
    assert preview == (payload.app_id, payload.platform, payload.title, text)
