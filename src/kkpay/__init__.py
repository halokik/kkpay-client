"""Public API for kkpay-client."""

from .client import AsyncKKPayClient, KKPayClient
from .direct import AsyncDirectPaymentService, DirectPaymentResult, DirectPaymentService
from .errors import (
    KKPayAPIError,
    KKPayCallbackError,
    KKPayChainError,
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
from .tron import (
    DEFAULT_TRON_API_URL,
    USDT_TRC20_CONTRACT,
    AsyncTronClient,
    ChainTransfer,
    TronClient,
    is_valid_tron_address,
    normalize_tron_address,
)

__all__ = [
    "AsyncKKPayClient",
    "AsyncDirectPaymentService",
    "AsyncPaymentService",
    "AsyncTronClient",
    "CallbackData",
    "ChainTransfer",
    "DEFAULT_TRON_API_URL",
    "create_fastapi_router",
    "DirectPaymentResult",
    "DirectPaymentService",
    "FulfillmentState",
    "KKPayAPIError",
    "KKPayCallbackError",
    "KKPayChainError",
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
    "TronClient",
    "USDT_TRC20_CONTRACT",
    "is_valid_tron_address",
    "WebhookResult",
    "make_signature",
    "normalize_tron_address",
    "verify_signature",
]

__version__ = "0.4.0"
