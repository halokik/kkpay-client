"""Typed values returned by KKPay-compatible gateways."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class TradeType(str):
    USDT_TRC20 = "usdt.trc20"
    TRX = "tron.trx"


class OrderStatus(IntEnum):
    WAITING = 1
    PAID = 2
    EXPIRED = 3
    CANCELLED = 4

    @classmethod
    def parse(cls, value: Any) -> "OrderStatus":
        return cls(int(value))


@dataclass(frozen=True)
class RetryPolicy:
    """Retry policy for transient network and gateway failures."""

    attempts: int = 3
    backoff_seconds: float = 0.25
    multiplier: float = 2.0
    max_backoff_seconds: float = 2.0
    status_codes: frozenset[int] = field(
        default_factory=lambda: frozenset({408, 425, 429, 500, 502, 503, 504})
    )

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("retry attempts must be at least 1")
        if self.backoff_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("retry backoff must not be negative")
        if self.multiplier < 1:
            raise ValueError("retry multiplier must be at least 1")

    def delay(self, attempt: int) -> float:
        """Return the delay before the next request after ``attempt`` failed."""
        delay = self.backoff_seconds * (self.multiplier ** max(0, attempt - 1))
        return min(delay, self.max_backoff_seconds)


def _required_text(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"missing gateway field: {key}")
    return value


@dataclass(frozen=True)
class Order:
    trade_id: str
    order_id: str
    status: OrderStatus
    amount: Any
    actual_amount: str
    token: str
    expiration_time: int
    payment_url: str
    raw: dict[str, Any] = field(repr=False)

    @property
    def address(self) -> str:
        """Receiving address returned in the gateway's legacy ``token`` field."""
        return self.token

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Order":
        return cls(
            trade_id=_required_text(data, "trade_id"),
            order_id=_required_text(data, "order_id"),
            status=OrderStatus.parse(data.get("status", 1)),
            amount=data.get("amount"),
            actual_amount=_required_text(data, "actual_amount"),
            token=_required_text(data, "token"),
            expiration_time=int(data.get("expiration_time") or 0),
            payment_url=_required_text(data, "payment_url"),
            raw=dict(data),
        )


@dataclass(frozen=True)
class QueryResult:
    trade_id: str
    status: OrderStatus
    trade_hash: str = ""
    return_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueryResult":
        return cls(
            trade_id=_required_text(data, "trade_id"),
            status=OrderStatus.parse(data.get("status", 1)),
            trade_hash=str(data.get("trade_hash") or data.get("block_transaction_id") or ""),
            return_url=str(data.get("return_url") or ""),
            raw=dict(data),
        )


@dataclass(frozen=True)
class CallbackData:
    trade_id: str
    order_id: str
    amount: Any
    actual_amount: str
    token: str
    block_transaction_id: str
    status: OrderStatus
    raw: dict[str, Any] = field(repr=False)

    @property
    def address(self) -> str:
        """Receiving address returned in the gateway's legacy ``token`` field."""
        return self.token

    @property
    def idempotency_key(self) -> str:
        """Stable event key suitable for a per-merchant idempotency store."""
        suffix = self.block_transaction_id or self.order_id
        return f"{self.trade_id}:{suffix}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CallbackData":
        return cls(
            trade_id=_required_text(data, "trade_id"),
            order_id=_required_text(data, "order_id"),
            amount=data.get("amount"),
            actual_amount=_required_text(data, "actual_amount"),
            token=_required_text(data, "token"),
            block_transaction_id=str(data.get("block_transaction_id") or ""),
            status=OrderStatus.parse(data.get("status", 1)),
            raw=dict(data),
        )
