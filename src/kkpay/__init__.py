"""Public API for kkpay-client."""

from .client import AsyncKKPayClient, KKPayClient
from .errors import (
    KKPayAPIError,
    KKPayCallbackError,
    KKPayConfigurationError,
    KKPayError,
    KKPayHTTPError,
    KKPayIdempotencyError,
    KKPayOrderConflictError,
    KKPayPaymentError,
    KKPayPaymentNotFoundError,
    KKPayQRCodeError,
    KKPaySignatureError,
)
from .fastapi import create_fastapi_router
from .idempotency import IdempotencyClaim, SQLiteIdempotencyStore
from .models import CallbackData, Order, OrderStatus, QueryResult, RetryPolicy, TradeType
from .payments import (
    AsyncPaymentService,
    FulfillmentState,
    Payment,
    PaymentClaim,
    PaymentService,
    SQLitePaymentStore,
    WebhookResult,
)
from .qr import make_qr_png, payment_qr_payload, payment_qr_png
from .signing import make_signature, verify_signature

__all__ = [
    "AsyncKKPayClient",
    "AsyncPaymentService",
    "CallbackData",
    "create_fastapi_router",
    "FulfillmentState",
    "KKPayAPIError",
    "KKPayCallbackError",
    "KKPayClient",
    "KKPayConfigurationError",
    "KKPayError",
    "KKPayHTTPError",
    "KKPayIdempotencyError",
    "KKPayOrderConflictError",
    "KKPayPaymentError",
    "KKPayPaymentNotFoundError",
    "KKPayQRCodeError",
    "KKPaySignatureError",
    "IdempotencyClaim",
    "make_qr_png",
    "Order",
    "OrderStatus",
    "Payment",
    "PaymentClaim",
    "PaymentService",
    "payment_qr_payload",
    "payment_qr_png",
    "QueryResult",
    "RetryPolicy",
    "SQLiteIdempotencyStore",
    "SQLitePaymentStore",
    "TradeType",
    "WebhookResult",
    "make_signature",
    "verify_signature",
]

__version__ = "0.3.0"
