"""Public API for kkpay-client."""

from .client import AsyncKKPayClient, KKPayClient
from .errors import (
    KKPayAPIError,
    KKPayCallbackError,
    KKPayConfigurationError,
    KKPayError,
    KKPayHTTPError,
    KKPayIdempotencyError,
    KKPaySignatureError,
)
from .idempotency import IdempotencyClaim, SQLiteIdempotencyStore
from .models import CallbackData, Order, OrderStatus, QueryResult, RetryPolicy, TradeType
from .signing import make_signature, verify_signature

__all__ = [
    "AsyncKKPayClient",
    "CallbackData",
    "KKPayAPIError",
    "KKPayCallbackError",
    "KKPayClient",
    "KKPayConfigurationError",
    "KKPayError",
    "KKPayHTTPError",
    "KKPayIdempotencyError",
    "KKPaySignatureError",
    "IdempotencyClaim",
    "Order",
    "OrderStatus",
    "QueryResult",
    "RetryPolicy",
    "SQLiteIdempotencyStore",
    "TradeType",
    "make_signature",
    "verify_signature",
]

__version__ = "0.2.0"
