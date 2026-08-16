from __future__ import annotations

from typing import Any

import httpx

from app.config import settings

CREEM_API_LIVE = "https://api.creem.io"
CREEM_API_TEST = "https://test-api.creem.io"


class CreemError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, trace_id: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.trace_id = trace_id


def creem_api_base() -> str:
    key = settings.creem_api_key or ""
    if key.startswith("creem_test_") or settings.creem_test_mode:
        return CREEM_API_TEST
    return CREEM_API_LIVE


def _headers() -> dict[str, str]:
    if not settings.creem_api_key:
        raise CreemError("CREEM_API_KEY is not configured")
    return {
        "x-api-key": settings.creem_api_key,
        "Content-Type": "application/json",
    }


def create_checkout(
    *,
    product_id: str,
    success_url: str,
    customer_email: str | None = None,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "product_id": product_id,
        "success_url": success_url,
    }
    if request_id:
        body["request_id"] = request_id
    if customer_email:
        body["customer"] = {"email": customer_email}
    if metadata:
        body["metadata"] = metadata

    with httpx.Client(timeout=30.0) as client:
        response = client.post(f"{creem_api_base()}/v1/checkouts", headers=_headers(), json=body)

    data = _parse_json(response)
    if response.status_code >= 400:
        raise CreemError(
            _error_message(data),
            status_code=response.status_code,
            trace_id=str(data.get("trace_id")) if isinstance(data, dict) else None,
        )
    return data


def create_customer_portal(customer_id: str) -> dict[str, Any]:
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{creem_api_base()}/v1/customers/billing",
            headers=_headers(),
            json={"customer_id": customer_id},
        )

    data = _parse_json(response)
    if response.status_code >= 400:
        raise CreemError(
            _error_message(data),
            status_code=response.status_code,
            trace_id=str(data.get("trace_id")) if isinstance(data, dict) else None,
        )
    return data


def _parse_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise CreemError(f"Invalid Creem response ({response.status_code})") from exc
    if not isinstance(payload, dict):
        raise CreemError(f"Unexpected Creem response ({response.status_code})")
    return payload


def _error_message(data: dict[str, Any]) -> str:
    message = data.get("message")
    if isinstance(message, list) and message:
        return "; ".join(str(item) for item in message)
    if isinstance(message, str) and message:
        return message
    error = data.get("error")
    if isinstance(error, str) and error:
        return error
    return "Creem API request failed"
