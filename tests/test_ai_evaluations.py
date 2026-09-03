from datetime import date
from decimal import Decimal

import pytest

from app.agents.filter_trade import apply_trade_filter
from app.agents.parse_alert import parse_alert
from app.models.tables import AiEvaluation, User
from app.schemas.trade import TradeIntent
from app.services.llm import LlmCompletion
from tests.db_helpers import insert_better_auth_user


def _completion(content: dict) -> LlmCompletion:
    return LlmCompletion(
        content=content,
        model="openai/gpt-4o-mini",
        generation_id="chatcmpl-1",
        prompt_tokens=20,
        completion_tokens=10,
        total_tokens=30,
        latency_ms=15,
        cost_usd=Decimal("0.000009"),
    )


@pytest.mark.asyncio
async def test_parse_alert_persists_evaluation(client, monkeypatch):
    _, db_factory = client
    db = db_factory()
    insert_better_auth_user(db, user_id="u-parse", email="parse@example.com", name="Parse")
    db.commit()

    async def fake_chat(**kwargs):
        return _completion(
            TradeIntent(
                action="buy_to_open",
                underlying="SPY",
                strike=580,
                expiration=date(2026, 6, 20),
                confidence=0.9,
                rationale="parsed",
            ).model_dump(mode="json")
        )

    monkeypatch.setattr("app.agents.parse_alert.llm_configured", lambda: True)
    monkeypatch.setattr("app.agents.parse_alert.chat_json_completion", fake_chat)

    intent = await parse_alert("BTO SPY", db=db, user_id="u-parse", alert_id=None)
    assert intent.underlying == "SPY"

    rows = db.query(AiEvaluation).filter_by(user_id="u-parse", kind="parse").all()
    assert len(rows) == 1
    assert rows[0].decision == "take"
    assert rows[0].prompt_tokens == 20
    assert rows[0].cost_usd == Decimal("0.000009")
    db.close()


@pytest.mark.asyncio
async def test_filter_persists_skip_evaluation(client, monkeypatch):
    _, db_factory = client
    db = db_factory()
    insert_better_auth_user(db, user_id="u-filter", email="filter@example.com", name="Filter")
    user = db.get(User, "u-filter")
    assert user is not None
    user.trade_filter_prompt = "skip calls"
    db.commit()

    async def fake_chat(**kwargs):
        return _completion({"take": False, "reason": "skip calls"})

    monkeypatch.setattr("app.agents.filter_trade.llm_configured", lambda: True)
    monkeypatch.setattr("app.agents.filter_trade.chat_json_completion", fake_chat)

    intent = TradeIntent(
        action="buy_to_open",
        underlying="SPY",
        option_type="call",
        strike=Decimal("580"),
        expiration=date(2026, 6, 20),
        quantity=1,
        confidence=0.95,
        rationale="parsed",
    )
    out = await apply_trade_filter(intent, user, db=db)
    assert out.action == "skip"

    rows = db.query(AiEvaluation).filter_by(user_id="u-filter", kind="filter").all()
    assert len(rows) == 1
    assert rows[0].decision == "skip"
    assert rows[0].total_tokens == 30
    db.close()
