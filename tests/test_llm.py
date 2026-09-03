from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from app.services.llm import LlmCompletion, chat_json_completion, estimate_cost_usd, llm_configured


class _SampleOutput(BaseModel):
    take: bool
    reason: str = ""


class _FakeStream:
    def __init__(self, *, output, usage, response_id, response_model):
        self.output = output
        self.usage = usage
        self._response_id = response_id
        self._response_model = response_model

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeStreamCM:
    def __init__(self, stream: _FakeStream):
        self._stream = stream

    async def __aenter__(self):
        return self._stream

    async def __aexit__(self, *args):
        return None


def test_estimate_cost_usd_uses_defaults():
    cost = estimate_cost_usd(model="openai/gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert cost == Decimal("0.750000")


def test_estimate_cost_usd_respects_env_overrides(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_price_input_per_m", 1.0)
    monkeypatch.setattr(settings, "ai_price_output_per_m", 2.0)
    cost = estimate_cost_usd(model="openai/gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=500_000)
    assert cost == Decimal("2.000000")


def test_llm_configured_requires_gateway_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_gateway_api_key", None)
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    assert llm_configured() is False

    monkeypatch.setattr(settings, "ai_gateway_api_key", "gw-test")
    assert llm_configured() is True


@pytest.mark.asyncio
async def test_chat_json_completion_extracts_usage(monkeypatch):
    from app.config import settings
    import app.services.llm as llm_mod

    monkeypatch.setattr(settings, "ai_gateway_api_key", "gw-test")
    monkeypatch.setattr(settings, "ai_model", "openai/gpt-4o-mini")

    usage = MagicMock()
    usage.input_tokens = 100
    usage.output_tokens = 40
    usage.total_tokens = 140

    fake = _FakeStream(
        output=_SampleOutput(take=True, reason="ok"),
        usage=usage,
        response_id="chatcmpl-abc",
        response_model="openai/gpt-4o-mini",
    )

    monkeypatch.setattr(llm_mod.ai, "stream", MagicMock(return_value=_FakeStreamCM(fake)))
    monkeypatch.setattr(llm_mod.ai, "get_provider", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(llm_mod.ai, "Model", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(llm_mod.ai, "system_message", MagicMock(side_effect=lambda t: t))
    monkeypatch.setattr(llm_mod.ai, "user_message", MagicMock(side_effect=lambda t: t))

    result = await chat_json_completion(
        system="sys",
        user="user",
        output_type=_SampleOutput,
        model="openai/gpt-4o",
    )
    assert isinstance(result, LlmCompletion)
    assert result.content == {"take": True, "reason": "ok"}
    assert result.generation_id == "chatcmpl-abc"
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 40
    assert result.total_tokens == 140
    assert result.cost_usd > 0
    assert llm_mod.ai.Model.call_args.kwargs["id"] == "openai/gpt-4o"
