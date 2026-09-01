import json
from typing import Any

import httpx

from app.config import settings


def llm_configured() -> bool:
    return bool(settings.ai_gateway_api_key or settings.openai_api_key)


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


async def chat_json_completion(
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    timeout: float = 30,
) -> dict[str, Any]:
    url, api_key, model = _resolve_api()
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
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("LLM response was not a JSON object")
        return parsed
