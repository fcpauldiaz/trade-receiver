from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.agents.filter_trade import (
    FilterDecision,
    SKIP_REASON_MAX,
    UNAVAILABLE_RATIONALE,
    apply_trade_filter,
)
from app.config import settings
from app.models.tables import User
from app.schemas.trade import TradeIntent


def _intent(**kwargs) -> TradeIntent:
    values: dict = {
        "action": "buy_to_open",
        "underlying": "SPY",
        "option_type": "call",
        "strike": Decimal("580"),
        "expiration": date(2026, 6, 20),
        "quantity": 1,
        "confidence": 0.95,
        "rationale": "parsed",
    }
    values.update(kwargs)
    return TradeIntent(**values)


def _user(**kwargs) -> User:
    return User(email="filter@example.com", **kwargs)


@pytest.mark.asyncio
async def test_empty_prompt_does_not_call_openai(monkeypatch):
    openai = AsyncMock()
    monkeypatch.setattr("app.agents.filter_trade._filter_with_llm", openai)
    intent = _intent()

    out = await apply_trade_filter(intent, _user(trade_filter_prompt=None))
    assert out.action == "buy_to_open"
    openai.assert_not_called()

    out = await apply_trade_filter(intent, _user(trade_filter_prompt="   "))
    assert out.action == "buy_to_open"
    openai.assert_not_called()


@pytest.mark.asyncio
async def test_take_true_leaves_intent_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "ai_gateway_api_key", "gw-test")
    monkeypatch.setattr(
        "app.agents.filter_trade._filter_with_llm",
        AsyncMock(return_value=FilterDecision(take=True, reason="matches rules")),
    )
    intent = _intent()
    out = await apply_trade_filter(intent, _user(trade_filter_prompt="only SPY"))
    assert out.action == "buy_to_open"
    assert out.strike == Decimal("580")
    assert out.quantity == 1


@pytest.mark.asyncio
async def test_take_false_skips_with_prefixed_reason(monkeypatch):
    monkeypatch.setattr(settings, "ai_gateway_api_key", "gw-test")
    monkeypatch.setattr(
        "app.agents.filter_trade._filter_with_llm",
        AsyncMock(return_value=FilterDecision(take=False, reason="skip calls")),
    )
    out = await apply_trade_filter(_intent(), _user(trade_filter_prompt="skip calls"))
    assert out.action == "skip"
    assert out.rationale == "user prompt: skip calls"


@pytest.mark.asyncio
async def test_fail_closed_without_api_key(monkeypatch):
    openai = AsyncMock()
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "ai_gateway_api_key", None)
    monkeypatch.setattr("app.agents.filter_trade._filter_with_llm", openai)
    out = await apply_trade_filter(_intent(), _user(trade_filter_prompt="only puts"))
    assert out.action == "skip"
    assert out.rationale == UNAVAILABLE_RATIONALE
    openai.assert_not_called()


@pytest.mark.asyncio
async def test_fail_closed_on_openai_error(monkeypatch):
    monkeypatch.setattr(settings, "ai_gateway_api_key", "gw-test")
    monkeypatch.setattr(
        "app.agents.filter_trade._filter_with_llm",
        AsyncMock(side_effect=RuntimeError("timeout")),
    )
    out = await apply_trade_filter(_intent(), _user(trade_filter_prompt="only puts"))
    assert out.action == "skip"
    assert out.rationale == UNAVAILABLE_RATIONALE


@pytest.mark.asyncio
async def test_skip_reason_is_truncated(monkeypatch):
    monkeypatch.setattr(settings, "ai_gateway_api_key", "gw-test")
    monkeypatch.setattr(
        "app.agents.filter_trade._filter_with_llm",
        AsyncMock(return_value=FilterDecision(take=False, reason="x" * 500)),
    )
    out = await apply_trade_filter(_intent(), _user(trade_filter_prompt="rules"))
    assert out.action == "skip"
    assert len(out.rationale) == SKIP_REASON_MAX
    assert out.rationale.startswith("user prompt: ")
