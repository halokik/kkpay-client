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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Order":
        return cls(
            trade_id=str(data.get("trade_id") or ""),
            order_id=str(data.get("order_id") or ""),
            status=OrderStatus.parse(data.get("status", 1)),
            amount=data.get("amount"),
            actual_amount=str(data.get("actual_amount") or ""),
            token=str(data.get("token") or ""),
            expiration_time=int(data.get("expiration_time") or 0),
            payment_url=str(data.get("payment_url") or ""),
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
            trade_id=str(data.get("trade_id") or ""),
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CallbackData":
        return cls(
            trade_id=str(data.get("trade_id") or ""),
            order_id=str(data.get("order_id") or ""),
            amount=data.get("amount"),
            actual_amount=str(data.get("actual_amount") or ""),
            token=str(data.get("token") or ""),
            block_transaction_id=str(data.get("block_transaction_id") or ""),
            status=OrderStatus.parse(data.get("status", 1)),
            raw=dict(data),
        )

