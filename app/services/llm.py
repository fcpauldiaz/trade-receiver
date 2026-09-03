import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TypeVar

import ai
from pydantic import BaseModel

from app.config import settings

# Default list prices for openai/gpt-4o-mini (USD per 1M tokens).
_DEFAULT_INPUT_PER_M = Decimal("0.15")
_DEFAULT_OUTPUT_PER_M = Decimal("0.60")

_MODEL_PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "openai/gpt-4o-mini": (_DEFAULT_INPUT_PER_M, _DEFAULT_OUTPUT_PER_M),
    "gpt-4o-mini": (_DEFAULT_INPUT_PER_M, _DEFAULT_OUTPUT_PER_M),
}

OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True)
class LlmCompletion:
    content: dict[str, Any]
    model: str
    generation_id: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    cost_usd: Decimal


def llm_configured() -> bool:
    return bool(settings.ai_gateway_api_key)


def estimate_cost_usd(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> Decimal:
    input_rate = (
        Decimal(str(settings.ai_price_input_per_m))
        if settings.ai_price_input_per_m is not None
        else None
    )
    output_rate = (
        Decimal(str(settings.ai_price_output_per_m))
        if settings.ai_price_output_per_m is not None
        else None
    )
    if input_rate is None or output_rate is None:
        defaults = _MODEL_PRICES.get(model, (_DEFAULT_INPUT_PER_M, _DEFAULT_OUTPUT_PER_M))
        if input_rate is None:
            input_rate = defaults[0]
        if output_rate is None:
            output_rate = defaults[1]
    cost = (Decimal(prompt_tokens) * input_rate + Decimal(completion_tokens) * output_rate) / Decimal(
        "1000000"
    )
    return cost.quantize(Decimal("0.000001"))


def _gateway_model(model_id: str | None = None) -> ai.Model:
    api_key = settings.ai_gateway_api_key
    if not api_key:
        raise RuntimeError("No LLM API key configured")
    provider = ai.get_provider("gateway", api_key=api_key)
    return ai.Model(id=model_id or settings.ai_model, provider=provider)


async def chat_json_completion(
    *,
    system: str,
    user: str,
    output_type: type[OutputT],
    timeout: float = 30,
    model: str | None = None,
) -> LlmCompletion:
    requested_model = (model or "").strip() or settings.ai_model
    gateway_model = _gateway_model(requested_model)
    messages = [
        ai.system_message(system),
        ai.user_message(user),
    ]
    started = time.perf_counter()
    async with asyncio.timeout(timeout):
        async with ai.stream(gateway_model, messages, output_type=output_type) as stream:
            async for _ in stream:
                pass
            parsed = stream.output
            usage = stream.usage
            generation_id = stream._response_id
            response_model = stream._response_model
    latency_ms = int((time.perf_counter() - started) * 1000)

    if not isinstance(parsed, BaseModel):
        raise ValueError("LLM response was not a structured object")

    prompt_tokens = int(usage.input_tokens) if usage is not None else 0
    completion_tokens = int(usage.output_tokens) if usage is not None else 0
    total_tokens = int(usage.total_tokens) if usage is not None else prompt_tokens + completion_tokens
    resolved_model = response_model or requested_model

    return LlmCompletion(
        content=parsed.model_dump(mode="json"),
        model=resolved_model,
        generation_id=generation_id if isinstance(generation_id, str) else None,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        cost_usd=estimate_cost_usd(
            model=resolved_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )
