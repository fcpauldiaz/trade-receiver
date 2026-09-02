"""Outbound WebSocket device bridge for NinjaTrader order delivery.

Delivery strategy: last-seen primary — orders are sent to the single most
recently active online device for the user. This avoids duplicate fills when
a user has multiple machines connected. Fan-out to all devices is intentionally
not implemented; use one active receiver per account.

The in-memory connection registry is process-local. Production runs a single
uvicorn worker (see Dockerfile), so this is sufficient. For multi-worker
deployments, replace the registry backend with Redis pub/sub.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SEC = 30.0
STALE_CONNECTION_SEC = 90.0
ORDER_ACK_TIMEOUT_SEC = 25.0
DELIVERED_ORDER_TTL_SEC = 3600.0


@dataclass
class LiveDeviceConnection:
    user_id: str
    device_id: str
    websocket: WebSocket
    last_seen: float = field(default_factory=time.monotonic)
    pending_acks: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class OrderPushResult:
    success: bool
    order_id: str | None
    raw_response: dict[str, Any]
    error: str | None = None
    transport: str = "wss"


class DeviceBridgeRegistry:
    """In-memory registry keyed by user_id then device_id."""

    def __init__(self) -> None:
        self._connections: dict[str, dict[str, LiveDeviceConnection]] = {}
        self._lock = asyncio.Lock()
        self._delivered_orders: dict[str, float] = {}

    async def register(self, user_id: str, device_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.setdefault(user_id, {})[device_id] = LiveDeviceConnection(
                user_id=user_id,
                device_id=device_id,
                websocket=websocket,
            )

    async def unregister(self, user_id: str, device_id: str) -> None:
        async with self._lock:
            conn = self._connections.get(user_id, {}).pop(device_id, None)
            if conn is not None:
                for future in conn.pending_acks.values():
                    if not future.done():
                        future.set_exception(WebSocketDisconnect())
            if self._connections.get(user_id) == {}:
                self._connections.pop(user_id, None)

    async def touch(self, user_id: str, device_id: str) -> None:
        async with self._lock:
            conn = self._connections.get(user_id, {}).get(device_id)
            if conn is not None:
                conn.last_seen = time.monotonic()

    def is_device_online(self, user_id: str, device_id: str) -> bool:
        return device_id in self._connections.get(user_id, {})

    def online_device_ids(self, user_id: str) -> list[str]:
        devices = self._connections.get(user_id, {})
        return sorted(devices.keys(), key=lambda did: devices[did].last_seen, reverse=True)

    def has_online_devices(self, user_id: str) -> bool:
        return bool(self._connections.get(user_id))

    def _primary_connection(self, user_id: str) -> LiveDeviceConnection | None:
        devices = self._connections.get(user_id)
        if not devices:
            return None
        return max(devices.values(), key=lambda c: c.last_seen)

    def _prune_delivered_orders(self, now: float) -> None:
        expired = [
            order_id
            for order_id, ts in self._delivered_orders.items()
            if now - ts > DELIVERED_ORDER_TTL_SEC
        ]
        for order_id in expired:
            self._delivered_orders.pop(order_id, None)

    def was_order_delivered(self, order_id: str) -> bool:
        self._prune_delivered_orders(time.monotonic())
        return order_id in self._delivered_orders

    def mark_order_delivered(self, order_id: str) -> None:
        self._delivered_orders[order_id] = time.monotonic()

    async def handle_incoming(
        self,
        user_id: str,
        device_id: str,
        msg: dict[str, Any] | str,
    ) -> bool:
        """Route an inbound device message. Returns True if handled."""
        if isinstance(msg, str):
            if msg.strip().lower() in {"ping", "pong"}:
                await self.touch(user_id, device_id)
                return True
            try:
                parsed = json.loads(msg)
            except json.JSONDecodeError:
                return False
            if not isinstance(parsed, dict):
                return False
            msg = parsed

        msg_type = str(msg.get("type") or "").lower()
        if msg_type in {"heartbeat", "pong"}:
            await self.touch(user_id, device_id)
            return True
        if msg_type == "ping":
            await self.touch(user_id, device_id)
            conn = self._connections.get(user_id, {}).get(device_id)
            if conn is not None:
                try:
                    await conn.websocket.send_json({"type": "pong"})
                except Exception:
                    pass
            return True
        if msg_type != "ack":
            return False

        ack_id = str(msg.get("id") or "")
        conn = self._connections.get(user_id, {}).get(device_id)
        if conn is None:
            return False

        await self.touch(user_id, device_id)
        future = conn.pending_acks.pop(ack_id, None)
        if future is not None and not future.done():
            future.set_result(msg)
        return True

    async def push_order(self, user_id: str, payload: dict[str, Any]) -> OrderPushResult | None:
        """Push order to the user's last-seen online device. Returns None if no device online."""
        order_id = str(payload.get("id") or "")
        if order_id and self.was_order_delivered(order_id):
            return OrderPushResult(
                success=True,
                order_id=order_id,
                raw_response={"status": "duplicate_skipped", "id": order_id},
                transport="wss",
            )

        conn = self._primary_connection(user_id)
        if conn is None:
            return None

        envelope = {"type": "order", "payload": payload}
        ack_future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        if order_id:
            conn.pending_acks[order_id] = ack_future

        try:
            await conn.websocket.send_json(envelope)
        except Exception as exc:
            conn.pending_acks.pop(order_id, None)
            logger.warning(
                "WSS send failed for user=%s device=%s: %s",
                user_id,
                conn.device_id,
                exc,
            )
            await self.unregister(user_id, conn.device_id)
            return OrderPushResult(
                success=False,
                order_id=order_id or None,
                raw_response={},
                error=f"device send failed: {exc}",
                transport="wss",
            )

        try:
            ack = await asyncio.wait_for(ack_future, timeout=ORDER_ACK_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            conn.pending_acks.pop(order_id, None)
            return OrderPushResult(
                success=False,
                order_id=order_id or None,
                raw_response={},
                error="device did not acknowledge order in time",
                transport="wss",
            )
        except Exception as exc:
            conn.pending_acks.pop(order_id, None)
            return OrderPushResult(
                success=False,
                order_id=order_id or None,
                raw_response={},
                error=str(exc),
                transport="wss",
            )

        success = bool(ack.get("success", True))
        ack_order_id = str(ack.get("id") or order_id or "")
        raw = ack if isinstance(ack, dict) else {"ack": ack}
        if success and ack_order_id:
            self.mark_order_delivered(ack_order_id)

        error = None
        if not success:
            error = str(ack.get("error") or ack.get("reason") or "device rejected order")

        return OrderPushResult(
            success=success,
            order_id=ack_order_id or order_id or None,
            raw_response={**raw, "device_id": conn.device_id, "transport": "wss"},
            error=error,
            transport="wss",
        )

    async def cleanup_stale(self) -> int:
        now = time.monotonic()
        removed = 0
        async with self._lock:
            for user_id in list(self._connections):
                for device_id in list(self._connections[user_id]):
                    conn = self._connections[user_id][device_id]
                    if now - conn.last_seen > STALE_CONNECTION_SEC:
                        self._connections[user_id].pop(device_id, None)
                        for future in conn.pending_acks.values():
                            if not future.done():
                                future.set_exception(asyncio.TimeoutError())
                        removed += 1
                        try:
                            await conn.websocket.close(code=1000, reason="stale connection")
                        except Exception:
                            pass
                if not self._connections[user_id]:
                    self._connections.pop(user_id, None)
        return removed


registry = DeviceBridgeRegistry()
_cleanup_task: asyncio.Task[None] | None = None


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
        try:
            removed = await registry.cleanup_stale()
            if removed:
                logger.info("device bridge removed %d stale connection(s)", removed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("device bridge stale cleanup failed")


async def start_device_bridge() -> None:
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_cleanup_loop())


async def stop_device_bridge() -> None:
    global _cleanup_task
    if _cleanup_task is not None:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
        _cleanup_task = None
