"""Asynchronous, durable Web Push delivery without tracker-facing failures.

A successful Web Push response confirms only that the push service accepted an
attempt. It does not confirm that iOS displayed a notification. The associated
leg claim is persisted before this service is invoked, so a network failure
never makes notification eligibility ambiguous.
"""

import asyncio
import json
import sqlite3
from collections.abc import Mapping
from typing import Protocol

from pywebpush import webpush

from .config import Settings
from .db import Database

DEFAULT_RETRY_BUDGET = 3
MAX_RETRY_DELAY_SECONDS = 1.0
_TRANSIENT_STATUS_CODES = {408, 425, 429}
_PERMANENT_INVALID_STATUS_CODES = {404, 410}


class WebPushTransport(Protocol):
    """Synchronous callable compatible with :func:`pywebpush.webpush`."""

    def __call__(
        self,
        *,
        subscription_info: Mapping[str, object],
        data: str,
        vapid_private_key: str,
        vapid_claims: dict[str, str | int],
    ) -> object: ...


def _pywebpush_transport(
    *,
    subscription_info: Mapping[str, object],
    data: str,
    vapid_private_key: str,
    vapid_claims: dict[str, str | int],
) -> object:
    """Adapt pywebpush to the narrow injectable transport protocol."""
    return webpush(
        subscription_info=subscription_info,
        data=data,
        vapid_private_key=vapid_private_key,
        vapid_claims=vapid_claims,
    )


def destination_notification_payload(destination_name: str) -> str:
    """Build the compact UTF-8 payload consumed by the root service worker."""
    return json.dumps(
        {
            "title": "Subway Tracker",
            "body": f"다음 역은 {destination_name}이에요. 하차를 준비하세요.",
            "url": "/",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _status_code(value: object) -> int | None:
    """Read a safe HTTP status from a response or pywebpush-style exception."""
    response = getattr(value, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(value, "status_code", None)
    return status if type(status) is int else None


def _is_retryable_status(status: int | None) -> bool:
    return status is None or status in _TRANSIENT_STATUS_CODES or status >= 500


def _safe_error(status: int | None) -> str:
    if status is None:
        return "web push network failure"
    return f"web push response status {status}"


class NotificationSender:
    """Deliver previously claimed leg notifications using a synchronous transport.

    Delivery inputs are read from the durable claim made by the caller. Calls
    never log an endpoint or subscription key and ordinary send/database errors
    are contained so tracker state transitions keep running.
    """

    def __init__(
        self,
        db: Database,
        settings: Settings,
        *,
        transport: WebPushTransport | None = None,
        retry_budget: int = DEFAULT_RETRY_BUDGET,
        retry_delay_seconds: float = 0.1,
    ) -> None:
        self._db = db
        self._settings = settings
        self._transport = transport or _pywebpush_transport
        self._retry_budget = max(1, retry_budget)
        self._retry_delay_seconds = min(
            MAX_RETRY_DELAY_SECONDS, max(0.0, retry_delay_seconds)
        )

    async def send_claimed_deliveries(
        self, journey_id: int, leg_idx: int, destination_name: str
    ) -> None:
        """Send pending rows from an already persisted claim.

        A completed ``sent`` row records an accepted Web Push attempt only; it
        deliberately makes no assertion about a user-visible iOS notification.
        """
        try:
            deliveries = self._db.get_pending_notification_deliveries(journey_id, leg_idx)
            payload = destination_notification_payload(destination_name)
            for delivery in deliveries:
                try:
                    await self._send_delivery(delivery, payload)
                except Exception:
                    # The caller is the journey tracker: no delivery failure can
                    # be allowed to change its state machine or stop its loop.
                    continue
        except Exception:
            # This includes persistence failures while reading queued work. There
            # is intentionally no secret-bearing diagnostic payload to log here.
            return

    async def _send_delivery(
        self, delivery: Mapping[str, object] | sqlite3.Row, payload: str
    ) -> None:
        journey_id = delivery["journey_id"]
        leg_idx = delivery["leg_idx"]
        endpoint = delivery["endpoint"]
        if type(journey_id) is not int or type(leg_idx) is not int or not isinstance(endpoint, str):
            return

        if not self._settings.web_push_enabled:
            self._db.record_notification_delivery_result(
                journey_id,
                leg_idx,
                endpoint,
                state="failed",
                last_error="web push disabled",
            )
            return

        subscription_info = {
            "endpoint": endpoint,
            "keys": {
                "p256dh": delivery["p256dh"],
                "auth": delivery["auth"],
            },
        }
        for attempt in range(1, self._retry_budget + 1):
            try:
                self._db.record_notification_delivery_attempt(journey_id, leg_idx, endpoint)
                response = await asyncio.to_thread(
                    self._transport,
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=self._settings.web_push_vapid_private_key,
                    vapid_claims={"sub": self._settings.web_push_vapid_subject},
                )
            except Exception as exc:
                status = _status_code(exc)
                error = _safe_error(status)
            else:
                status = _status_code(response)
                if status is None or 200 <= status < 300:
                    self._db.record_notification_delivery_result(
                        journey_id, leg_idx, endpoint, state="sent"
                    )
                    return
                error = _safe_error(status)

            if status in _PERMANENT_INVALID_STATUS_CODES:
                self._db.record_notification_delivery_result(
                    journey_id, leg_idx, endpoint, state="failed", last_error=error
                )
                self._db.delete_push_subscription(endpoint)
                return

            if _is_retryable_status(status) and attempt < self._retry_budget:
                self._db.record_notification_delivery_result(
                    journey_id, leg_idx, endpoint, state="retryable", last_error=error
                )
                await asyncio.sleep(self._retry_delay_seconds)
                continue

            self._db.record_notification_delivery_result(
                journey_id, leg_idx, endpoint, state="failed", last_error=error
            )
            return
