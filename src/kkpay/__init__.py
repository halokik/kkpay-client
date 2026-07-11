"""Public API for kkpay-client."""

from .client import AsyncKKPayClient, KKPayClient
from .errors import (
    KKPayAPIError,
    KKPayConfigurationError,
    KKPayError,
    KKPayHTTPError,
    KKPaySignatureError,
)
from .models import CallbackData, Order, OrderStatus, QueryResult, TradeType
from .signing import make_signature, verify_signature

__all__ = [
    "AsyncKKPayClient",
    "CallbackData",
    "KKPayAPIError",
    "KKPayClient",
    "KKPayConfigurationError",
    "KKPayError",
    "KKPayHTTPError",
    "KKPaySignatureError",
    "Order",
    "OrderStatus",
    "QueryResult",
    "TradeType",
    "make_signature",
    "verify_signature",
]

__version__ = "0.1.0"

