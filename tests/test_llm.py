from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.llm import LlmCompletion, chat_json_completion, estimate_cost_usd


def test_estimate_cost_usd_uses_defaults():
    cost = estimate_cost_usd(model="openai/gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert cost == Decimal("0.750000")


def test_estimate_cost_usd_respects_env_overrides(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_price_input_per_m", 1.0)
    monkeypatch.setattr(settings, "ai_price_output_per_m", 2.0)
    cost = estimate_cost_usd(model="openai/gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=500_000)
    assert cost == Decimal("2.000000")


@pytest.mark.asyncio
async def test_chat_json_completion_extracts_usage(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_gateway_api_key", "gw-test")
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "ai_model", "openai/gpt-4o-mini")

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "id": "chatcmpl-abc",
        "model": "openai/gpt-4o-mini",
        "choices": [{"message": {"content": '{"take": true, "reason": "ok"}'}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
    }

    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.post = AsyncMock(return_value=response)
    monkeypatch.setattr(httpx, "AsyncClient", MagicMock(return_value=client))

    result = await chat_json_completion(
        system="sys",
        user="user",
        schema={"type": "object"},
    )
    assert isinstance(result, LlmCompletion)
    assert result.content == {"take": True, "reason": "ok"}
    assert result.generation_id == "chatcmpl-abc"
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 40
    assert result.total_tokens == 140
    assert result.cost_usd > 0
