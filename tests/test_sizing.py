from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.parse_alert import parse_alert_rules
from app.models.tables import User
from app.schemas.trade import ValidatedTrade
from app.services.compute_quantity import compute_quantity


def test_parse_extracts_quantity_from_alert():
    intent = parse_alert_rules("BTO SPY 580C 6/20 x2")
    assert intent.quantity == 2

    intent2 = parse_alert_rules("BTO 3 contracts QQQ 480P 07/18")
    assert intent2.quantity == 3


def test_parse_defaults_quantity_to_one():
    intent = parse_alert_rules("BTO SPY 580C 6/20 @ 2.50")
    assert intent.quantity == 1


def _validated(quantity: int = 1, ask: Decimal | None = Decimal("2.50")) -> ValidatedTrade:
    return ValidatedTrade(
        action="buy_to_open",
        underlying="SPY",
        option_type="call",
        strike=Decimal("580"),
        expiration=date(2026, 6, 20),
        quantity=quantity,
        order_type="market",
        limit_price=None,
        confidence=0.9,
        rationale="test",
        broker="tradier",
        contract_symbol="SPY",
        ask=ask,
    )


@pytest.mark.asyncio
async def test_compute_quantity_alert_inferred():
    user = User(email="a@b.com", sizing_mode="alert_inferred", max_contracts=5)
    adapter = MagicMock()
    adapter.get_account_equity = AsyncMock()
    qty, skip = await compute_quantity(user, _validated(quantity=3), adapter)
    assert skip is None
    assert qty == 3


@pytest.mark.asyncio
async def test_compute_quantity_fixed():
    user = User(email="a@b.com", sizing_mode="fixed", fixed_contracts=4, max_contracts=10)
    adapter = MagicMock()
    qty, skip = await compute_quantity(user, _validated(quantity=1), adapter)
    assert skip is None
    assert qty == 4


@pytest.mark.asyncio
async def test_compute_quantity_caps_at_max_contracts():
    user = User(email="a@b.com", sizing_mode="fixed", fixed_contracts=10, max_contracts=3)
    adapter = MagicMock()
    qty, skip = await compute_quantity(user, _validated(), adapter)
    assert qty == 3


@pytest.mark.asyncio
async def test_compute_quantity_risk_percent():
    user = User(email="a@b.com", sizing_mode="risk_percent", risk_percent=2.0, max_contracts=10)
    adapter = MagicMock()
    adapter.get_account_equity = AsyncMock(return_value=Decimal("10000"))
    qty, skip = await compute_quantity(user, _validated(ask=Decimal("2.00")), adapter)
    assert skip is None
    assert qty == 1


@pytest.mark.asyncio
async def test_compute_quantity_risk_skips_without_equity():
    user = User(email="a@b.com", sizing_mode="risk_percent", risk_percent=1.0, max_contracts=5)
    adapter = MagicMock()
    adapter.get_account_equity = AsyncMock(return_value=None)
    qty, skip = await compute_quantity(user, _validated(), adapter)
    assert qty == 0
    assert skip is not None
