"""Thin async client for the Cashfree Payment Gateway (PG) API."""

import base64
import hashlib
import hmac
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class CashfreeError(Exception):
    """A Cashfree call failed, or Cashfree isn't configured."""

    def __init__(self, message: str, status_code: int | None = None, *, configured: bool = True):
        super().__init__(message)
        self.status_code = status_code
        self.configured = configured


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    for key in ("message", "error_description", "error", "reason"):
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, str) and value.strip():
            return value
    return f"HTTP {response.status_code}"


class CashfreeClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self._settings = settings
        self._http = http or httpx.AsyncClient(
            base_url=settings.cashfree_base_url,
            timeout=httpx.Timeout(20.0, connect=10.0),
        )

    @property
    def mode(self) -> str:
        return self._settings.cashfree_env

    async def aclose(self) -> None:
        await self._http.aclose()

    def _headers(self, idempotency_key: str | None) -> dict[str, str]:
        headers = {
            "x-api-version": self._settings.cashfree_api_version,
            "x-client-id": self._settings.cashfree_app_id,
            "x-client-secret": self._settings.cashfree_secret_key.get_secret_value(),
            "x-request-id": str(uuid4()),
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["x-idempotency-key"] = idempotency_key
        return headers

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None, idempotency_key: str | None = None
    ) -> Any:
        if not self._settings.cashfree_enabled:
            raise CashfreeError("Cashfree credentials are not configured", configured=False)
        try:
            response = await self._http.request(method, path, json=json, headers=self._headers(idempotency_key))
        except httpx.HTTPError as exc:
            raise CashfreeError(f"Could not reach Cashfree ({exc.__class__.__name__})") from exc

        if response.status_code >= 400:
            raise CashfreeError(_error_message(response), response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise CashfreeError("Cashfree returned invalid JSON") from exc

    async def create_order(
        self,
        *,
        order_id: str,
        amount_paise: int,
        customer_id: str,
        customer_name: str,
        customer_phone: str,
        customer_email: str,
        note: str,
        tags: dict[str, str],
        return_url: str,
        notify_url: str | None,
        expires_at: datetime,
    ) -> dict[str, Any]:
        order_meta: dict[str, str] = {"return_url": return_url}
        if notify_url:
            order_meta["notify_url"] = notify_url

        payload = {
            "order_id": order_id,
            "order_amount": round(amount_paise / 100, 2),
            "order_currency": "INR",
            "customer_details": {
                "customer_id": customer_id,
                "customer_name": customer_name[:100],
                "customer_phone": customer_phone,
                "customer_email": customer_email,
            },
            "order_meta": order_meta,
            "order_note": note[:200],
            "order_tags": {k[:40]: v[:200] for k, v in tags.items()},
            "order_expiry_time": expires_at.isoformat(timespec="seconds"),
        }
        # The order id doubles as the idempotency key, so a retried request can't create two orders.
        return await self._request("POST", "/orders", json=payload, idempotency_key=order_id)

    async def get_order(self, order_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/orders/{order_id}")

    async def get_payments(self, order_id: str) -> list[dict[str, Any]]:
        result = await self._request("GET", f"/orders/{order_id}/payments")
        return result if isinstance(result, list) else []

    def verify_webhook_signature(self, *, raw_body: bytes, timestamp: str, signature: str) -> bool:
        """Cashfree signs webhooks as base64(HMAC-SHA256(secret, timestamp + raw body))."""
        secret = self._settings.cashfree_secret_key.get_secret_value()
        if not (secret and timestamp and signature):
            return False
        digest = hmac.new(secret.encode(), timestamp.encode() + raw_body, hashlib.sha256).digest()
        return hmac.compare_digest(base64.b64encode(digest).decode(), signature)
