import json
from typing import Any

from app.schemas.trade import DiscordWebhookPayload, WebhookPayload
from app.services.eterminal_signal import is_eterminal_envelope


def normalize_webhook_body(body: dict[str, Any]) -> tuple[str, WebhookPayload]:
    if is_eterminal_envelope(body):
        signal = body.get("signal") if isinstance(body.get("signal"), dict) else {}
        signal_id = str(signal.get("id") or "")
        side = str(signal.get("side") or "")
        price = signal.get("price")
        title = f"eterminal {body.get('type', 'event')}"
        body_text = f"signal_id={signal_id} side={side} price={price}"
        payload = WebhookPayload(
            app_id="eterminal",
            title=title,
            body=body_text,
            delivered_date_iso=str(body.get("firedAt") or ""),
            platform="eterminal",
        )
        text = "\n".join(p for p in [title, body_text] if p)
        return text, payload

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
