from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.brokers.schwab import SchwabAdapter
from app.brokers.tradier import TradierAdapter
from app.models.tables import User
from app.schemas.trade import ValidatedTrade
from app.services.compute_quantity import compute_quantity, quantity_for_notional
from app.services.eterminal_signal import (
    eterminal_idempotency_key,
    is_eterminal_envelope,
    map_eterminal_signal,
)
from app.services.webhook_normalize import normalize_webhook_body


def _signal_body(*, side: str = "long", signal_id: str = "sig-1", price: float = 6324.0) -> dict:
    return {
        "type": "signal",
        "firedAt": "2026-07-21T15:00:00.000Z",
        "session": "rth",
        "caution": None,
        "signal": {
            "id": signal_id,
            "time": 1721596500,
            "price": price,
            "shape": "triangle",
            "side": side,
            "variant": "filled",
            "color": "green",
            "source": "spx3",
        },
        "context": {
            "currentPrice": price,
            "bias": "bullish",
            "gap": 3.2,
            "retailValue": 12.1,
            "instValue": 8.9,
        },
    }


def test_is_eterminal_envelope():
    assert is_eterminal_envelope(_signal_body())
    assert is_eterminal_envelope({"type": "test", "firedAt": "2026-01-01T00:00:00Z"})
    assert not is_eterminal_envelope({"title": "Alert", "body": "BTO SPY"})


def test_normalize_eterminal_envelope():
    text, payload = normalize_webhook_body(_signal_body())
    assert payload.platform == "eterminal"
    assert "sig-1" in text
    assert "long" in text


def test_map_long_signal_to_call():
    intent = map_eterminal_signal(_signal_body(side="long", price=6324))
    assert intent is not None
    assert intent.action == "buy_to_open"
    assert intent.underlying == "SPX"
    assert intent.option_type == "call"
    assert intent.strike == Decimal("6325")
    assert intent.take_profit_pct == Decimal("0.3")
    assert intent.notional_usd == Decimal("1000")
    assert intent.source == "eterminal"


def test_map_short_signal_to_put():
    intent = map_eterminal_signal(_signal_body(side="short", price=6326))
    assert intent is not None
    assert intent.option_type == "put"
    assert intent.strike == Decimal("6325")


def test_map_skips_non_signal_events():
    body = _signal_body()
    body["type"] = "test"
    assert map_eterminal_signal(body) is None

    body = _signal_body()
    body["type"] = "setup"
    assert map_eterminal_signal(body) is None


def test_map_skips_invalid_side():
    body = _signal_body()
    body["signal"]["side"] = "flat"
    assert map_eterminal_signal(body) is None


def test_eterminal_idempotency_stable_on_signal_id():
    a = eterminal_idempotency_key("user-1", _signal_body(signal_id="abc"))
    b = eterminal_idempotency_key("user-1", _signal_body(signal_id="abc"))
    c = eterminal_idempotency_key("user-1", _signal_body(signal_id="xyz"))
    assert a == b
    assert a != c


def test_quantity_for_notional_1k():
    # ask $5 → $500/contract → floor(1000/500)=2
    assert quantity_for_notional(Decimal("5.00"), Decimal("1000"), max_contracts=10) == 2
    # ask $12 → $1200/contract → floor=0 → min 1
    assert quantity_for_notional(Decimal("12.00"), Decimal("1000"), max_contracts=10) == 1


@pytest.mark.asyncio
async def test_compute_quantity_uses_notional_usd():
    user = User(email="a@b.com", sizing_mode="alert_inferred", max_contracts=10)
    validated = ValidatedTrade(
        action="buy_to_open",
        underlying="SPX",
        option_type="call",
        strike=Decimal("6325"),
        expiration=date.today(),
        quantity=1,
        order_type="market",
        limit_price=None,
        confidence=1.0,
        rationale="test",
        broker="tradier",
        contract_symbol="SPXW",
        ask=Decimal("5.00"),
        notional_usd=Decimal("1000"),
        take_profit_pct=Decimal("0.30"),
        source="eterminal",
    )
    adapter = MagicMock()
    qty, skip = await compute_quantity(user, validated, adapter)
    assert skip is None
    assert qty == 2


@pytest.mark.asyncio
async def test_schwab_oto_payload_shape(monkeypatch):
    adapter = SchwabAdapter(access_token="t", account_hash="acct")
    captured: dict = {}

    async def fake_place(order_body: dict):
        captured.update(order_body)
        from app.brokers.base import OrderResult

        return OrderResult(success=True, order_id="1", fill_price=None, raw_response=order_body)

    monkeypatch.setattr(adapter, "_place_live_order", fake_place)
    from app.brokers.base import OptionContract

    contract = OptionContract(
        symbol="SPXW260721C06325000",
        underlying="SPX",
        option_type="call",
        strike=Decimal("6325"),
        expiration=date.today(),
        bid=Decimal("4.8"),
        ask=Decimal("5.0"),
        open_interest=100,
    )
    result = await adapter.place_order_with_take_profit(
        contract, 2, "buy_to_open", "live", take_profit_price=Decimal("6.50")
    )
    assert result.success
    assert captured["orderStrategyType"] == "TRIGGER"
    assert captured["orderType"] == "MARKET"
    child = captured["childOrderStrategies"][0]
    assert child["orderType"] == "LIMIT"
    assert child["price"] == 6.5
    assert child["orderLegCollection"][0]["instruction"] == "SELL_TO_CLOSE"


@pytest.mark.asyncio
async def test_tradier_market_entry_then_tp(monkeypatch):
    adapter = TradierAdapter(access_token="t", account_id="acct")
    adapter.base = "https://api.tradier.com/v1"
    posts: list[dict] = []

    class FakeResp:
        def __init__(self, order_id: str):
            self.status_code = 200
            self._order_id = order_id
            self.content = b'{"order":{"id":"x"}}'

        def json(self):
            return {"order": {"id": self._order_id}}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, data=None):
            posts.append(dict(data or {}))
            return FakeResp("99" if len(posts) == 1 else "100")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    from app.brokers.base import OptionContract

    contract = OptionContract(
        symbol="SPXW260721C06325000",
        underlying="SPX",
        option_type="call",
        strike=Decimal("6325"),
        expiration=date.today(),
        bid=Decimal("4.8"),
        ask=Decimal("5.0"),
        open_interest=100,
    )
    result = await adapter.place_order_with_take_profit(
        contract, 2, "buy_to_open", "live", take_profit_price=Decimal("6.50")
    )
    assert result.success
    assert len(posts) == 2
    assert posts[0]["type"] == "market"
    assert posts[0]["side"] == "buy_to_open"
    assert posts[1]["type"] == "limit"
    assert posts[1]["side"] == "sell_to_close"
    assert posts[1]["price"] == "6.50"
    assert result.raw_response["entry"] == "market"
    assert result.raw_response["take_profit_order_id"] == "100"


@pytest.mark.asyncio
async def test_execute_trade_calls_oto(monkeypatch):
    from app.brokers.base import OptionContract, OrderResult
    from app.models.tables import InboundAlert
    from app.services.execute_trade import execute_trade

    class TpAdapter:
        name = "tradier"

        def __init__(self):
            self.called_tp: Decimal | None = None

        async def place_order(self, *args, **kwargs):
            raise AssertionError("should use take-profit path")

        async def place_order_with_take_profit(
            self, contract, quantity, side, mode, *, take_profit_price: Decimal
        ):
            self.called_tp = take_profit_price
            return OrderResult(
                success=True,
                order_id="oto-1",
                fill_price=Decimal("5.00"),
                raw_response={"take_profit_price": float(take_profit_price)},
            )

        async def get_order_status(self, order_id: str):
            return {"status": "FILLED"}

    adapter = TpAdapter()
    user = User(email="a@b.com", default_mode="paper", live_trading_enabled=False)
    user.id = "u1"
    alert = InboundAlert(
        user_id="u1",
        idempotency_key="k",
        raw_payload="{}",
        normalized_text="t",
        subscription_active=True,
    )
    alert.id = "a1"

    validated = ValidatedTrade(
        action="buy_to_open",
        underlying="SPX",
        option_type="call",
        strike=Decimal("6325"),
        expiration=date.today(),
        quantity=2,
        order_type="market",
        limit_price=None,
        confidence=1.0,
        rationale="eterminal",
        broker="tradier",
        contract_symbol="SPXW260721C06325000",
        ask=Decimal("5.00"),
        bid=Decimal("4.80"),
        take_profit_pct=Decimal("0.30"),
        source="eterminal",
        notional_usd=Decimal("1000"),
    )

    db = MagicMock()
    db.add = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()

    execution = await execute_trade(db, user, alert, validated, adapter)
    assert adapter.called_tp == Decimal("6.50")
    assert "take_profit_price" in execution.broker_response_json
