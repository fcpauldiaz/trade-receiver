import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from app.config import settings

# Default list prices for openai/gpt-4o-mini (USD per 1M tokens).
_DEFAULT_INPUT_PER_M = Decimal("0.15")
_DEFAULT_OUTPUT_PER_M = Decimal("0.60")

_MODEL_PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "openai/gpt-4o-mini": (_DEFAULT_INPUT_PER_M, _DEFAULT_OUTPUT_PER_M),
    "gpt-4o-mini": (_DEFAULT_INPUT_PER_M, _DEFAULT_OUTPUT_PER_M),
}


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
    return bool(settings.ai_gateway_api_key or settings.openai_api_key)


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


def _resolve_api() -> tuple[str, str, str]:
    if settings.ai_gateway_api_key:
        base = settings.ai_gateway_base_url.rstrip("/")
        return (
            f"{base}/chat/completions",
            settings.ai_gateway_api_key,
            settings.ai_model,
        )
    if settings.openai_api_key:
        return (
            "https://api.openai.com/v1/chat/completions",
            settings.openai_api_key,
            settings.ai_model.removeprefix("openai/"),
        )
    raise RuntimeError("No LLM API key configured")


def _usage_ints(usage: dict[str, Any] | None) -> tuple[int, int, int]:
    if not isinstance(usage, dict):
        return 0, 0, 0
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    return prompt, completion, total


async def chat_json_completion(
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    timeout: float = 30,
) -> LlmCompletion:
    url, api_key, model = _resolve_api()
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user + "\n\nSchema:\n" + json.dumps(schema)},
                ],
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        body = resp.json()
        latency_ms = int((time.perf_counter() - started) * 1000)
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("LLM response was not a JSON object")
        prompt_tokens, completion_tokens, total_tokens = _usage_ints(body.get("usage"))
        generation_id = body.get("id")
        return LlmCompletion(
            content=parsed,
            model=str(body.get("model") or model),
            generation_id=generation_id if isinstance(generation_id, str) else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            cost_usd=estimate_cost_usd(
                model=str(body.get("model") or model),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
        )
