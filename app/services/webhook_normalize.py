import json
from typing import Any

from app.schemas.trade import DiscordWebhookPayload, WebhookPayload


def normalize_webhook_body(body: dict[str, Any]) -> tuple[str, WebhookPayload]:
    if "embeds" in body:
        discord = DiscordWebhookPayload.model_validate(body)
        parts: list[str] = []
        app_id = ""
        for embed in discord.embeds:
            if embed.title:
                parts.append(embed.title)
            if embed.description:
                parts.append(embed.description)
            if embed.footer and embed.footer.get("text"):
                app_id = str(embed.footer["text"])
        text = "\n\n".join(p for p in parts if p)
        payload = WebhookPayload(
            app_id=app_id,
            title=discord.embeds[0].title if discord.embeds else "",
            body=discord.embeds[0].description if discord.embeds else "",
        )
        return text, payload

    payload = WebhookPayload.model_validate(body)
    text = "\n".join(p for p in [payload.title, payload.subtitle, payload.body] if p)
    return text, payload


def idempotency_key(user_id: str, payload: WebhookPayload) -> str:
    raw = json.dumps(
        {
            "user_id": user_id,
            "title": payload.title,
            "subtitle": payload.subtitle,
            "body": payload.body,
            "delivered_date": payload.delivered_date,
        },
        sort_keys=True,
    )
    import hashlib

    return hashlib.sha256(raw.encode()).hexdigest()
