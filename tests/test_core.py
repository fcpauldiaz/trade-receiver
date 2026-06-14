import pytest
from decimal import Decimal

from app.agents.parse_alert import parse_alert_rules
from app.services.entitlements import can_process_trades, verify_lemon_squeezy_signature
from app.services.webhook_normalize import normalize_webhook_body
from app.models.tables import Subscription, User


def test_parse_bto_alert():
    intent = parse_alert_rules("BTO SPY 580C 6/20 @ 2.50")
    assert intent.action == "buy_to_open"
    assert intent.underlying == "SPY"
    assert intent.strike == Decimal("580")


def test_normalize_generic_webhook():
    text, payload = normalize_webhook_body({
        "title": "Alert",
        "body": "BTO SPY 580C",
        "app_id": "com.discord",
    })
    assert "Alert" in text
    assert payload.title == "Alert"


def test_normalize_discord_embed():
    text, payload = normalize_webhook_body({
        "embeds": [{"title": "Trade", "description": "BTO QQQ 480P", "footer": {"text": "discord"}}]
    })
    assert "Trade" in text


def test_can_process_trades_inactive():
    user = User(email="a@b.com", webhook_enabled=True)
    user.subscription = Subscription(status="none")
    assert can_process_trades(user) is False


def test_can_process_trades_active():
    user = User(email="a@b.com", webhook_enabled=True)
    user.subscription = Subscription(status="active")
    assert can_process_trades(user) is True


def test_lemon_signature():
    secret = "test-secret"
    payload = b'{"test": true}'
    import hashlib, hmac
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert verify_lemon_squeezy_signature(payload, sig, secret)
