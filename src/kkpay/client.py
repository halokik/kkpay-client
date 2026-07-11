"""Synchronous and asynchronous KKPay clients."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from .errors import KKPayAPIError, KKPayConfigurationError, KKPayHTTPError, KKPaySignatureError
from .models import CallbackData, Order, OrderStatus, QueryResult, TradeType
from .signing import make_signature, verify_signature


class _ClientBase:
    def __init__(
        self,
        base_url: str,
        merchant_id: str,
        api_token: str,
        *,
        timeout: float = 15.0,
        allow_insecure_http: bool = False,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.merchant_id = str(merchant_id or "").strip()
        self._api_token = str(api_token or "").strip()
        self.timeout = float(timeout)
        self._transport = transport
        self._validate_config(allow_insecure_http)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self.base_url!r}, merchant_id={self.merchant_id!r}, api_token='***')"

    def _validate_config(self, allow_insecure_http: bool) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise KKPayConfigurationError("base_url must be an absolute HTTP(S) URL")
        if not self.merchant_id:
            raise KKPayConfigurationError("merchant_id must not be empty")
        if not self._api_token:
            raise KKPayConfigurationError("api_token must not be empty")
        local_hosts = {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme == "http" and parsed.hostname not in local_hosts and not allow_insecure_http:
            raise KKPayConfigurationError(
                "public HTTP gateway refused; use HTTPS or explicitly set allow_insecure_http=True"
            )

    def _signed(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        data["signature"] = make_signature(data, self._api_token)
        return data

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise KKPayHTTPError(f"gateway returned HTTP {exc.response.status_code}") from exc
        except (ValueError, httpx.HTTPError) as exc:
            raise KKPayHTTPError(f"invalid gateway response: {exc}") from exc
        if not isinstance(data, dict):
            raise KKPayHTTPError("gateway response must be a JSON object")
        return data

    @staticmethod
    def _unwrap(data: dict[str, Any]) -> dict[str, Any]:
        try:
            code = int(data.get("status_code", 0))
        except (TypeError, ValueError):
            code = 0
        result = data.get("data")
        if code != 200 or not isinstance(result, dict):
            raise KKPayAPIError(
                str(data.get("message") or "gateway rejected the request"),
                status_code=code or None,
                request_id=str(data.get("request_id") or ""),
            )
        return result

    def create_payload(
        self,
        *,
        order_id: str,
        amount: Any,
        notify_url: str,
        redirect_url: str = "",
        trade_type: str = TradeType.USDT_TRC20,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        if not str(order_id or "").strip():
            raise ValueError("order_id must not be empty")
        if not str(notify_url or "").strip():
            raise ValueError("notify_url must not be empty")
        if trade_type not in {TradeType.USDT_TRC20, TradeType.TRX}:
            raise ValueError("trade_type must be 'usdt.trc20' or 'tron.trx'")
        payload: dict[str, Any] = {
            "merchant_id": self.merchant_id,
            "order_id": str(order_id).strip(),
            "amount": amount,
            "notify_url": str(notify_url).strip(),
            "redirect_url": str(redirect_url or "").strip(),
            "trade_type": trade_type,
        }
        if timeout is not None:
            payload["timeout"] = int(timeout)
        return self._signed(payload)

    def verify_callback(
        self,
        payload: Mapping[str, Any],
        *,
        require_paid: bool = True,
        expected_order_id: str | None = None,
        expected_trade_id: str | None = None,
    ) -> CallbackData:
        data = dict(payload)
        if not verify_signature(data, self._api_token):
            raise KKPaySignatureError("invalid callback signature")
        callback = CallbackData.from_dict(data)
        if require_paid and callback.status is not OrderStatus.PAID:
            raise KKPaySignatureError(f"callback is not paid: status={int(callback.status)}")
        if expected_order_id is not None and callback.order_id != expected_order_id:
            raise KKPaySignatureError("callback order_id does not match")
        if expected_trade_id is not None and callback.trade_id != expected_trade_id:
            raise KKPaySignatureError("callback trade_id does not match")
        return callback


class KKPayClient(_ClientBase):
    """Blocking KKPay client."""

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                response = client.request(method, f"{self.base_url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise KKPayHTTPError(f"gateway request failed: {exc}") from exc
        return self._json(response)

    def create_order(self, **kwargs: Any) -> Order:
        payload = self.create_payload(**kwargs)
        data = self._request("POST", "/api/v1/order/create-transaction", json=payload)
        return Order.from_dict(self._unwrap(data))

    def query_order(self, trade_id: str) -> QueryResult:
        data = self._request("GET", f"/pay/check-status/{quote(str(trade_id), safe='')}")
        return QueryResult.from_dict(data)

    def cancel_order(self, trade_id: str) -> str:
        payload = self._signed({"merchant_id": self.merchant_id, "trade_id": str(trade_id)})
        data = self._request("POST", "/api/v1/order/cancel-transaction", json=payload)
        return str(self._unwrap(data).get("trade_id") or trade_id)


class AsyncKKPayClient(_ClientBase):
    """Asyncio-native KKPay client."""

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
                response = await client.request(method, f"{self.base_url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise KKPayHTTPError(f"gateway request failed: {exc}") from exc
        return self._json(response)

    async def create_order(self, **kwargs: Any) -> Order:
        payload = self.create_payload(**kwargs)
        data = await self._request("POST", "/api/v1/order/create-transaction", json=payload)
        return Order.from_dict(self._unwrap(data))

    async def query_order(self, trade_id: str) -> QueryResult:
        data = await self._request("GET", f"/pay/check-status/{quote(str(trade_id), safe='')}")
        return QueryResult.from_dict(data)

    async def cancel_order(self, trade_id: str) -> str:
        payload = self._signed({"merchant_id": self.merchant_id, "trade_id": str(trade_id)})
        data = await self._request("POST", "/api/v1/order/cancel-transaction", json=payload)
        return str(self._unwrap(data).get("trade_id") or trade_id)

