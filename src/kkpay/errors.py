"""Exception hierarchy."""

from __future__ import annotations


class KKPayError(Exception):
    """Base error for all client failures."""


class KKPayConfigurationError(KKPayError, ValueError):
    """The client configuration is incomplete or unsafe."""


class KKPayHTTPError(KKPayError):
    """The gateway could not be reached or returned invalid HTTP."""


class KKPayAPIError(KKPayError):
    """The gateway returned a valid error response."""

    def __init__(self, message: str, *, status_code: int | None = None, request_id: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class KKPaySignatureError(KKPayError):
    """A callback signature is missing or invalid."""


class KKPayCallbackError(KKPayError, ValueError):
    """A signed callback is malformed or does not match the local order."""


class KKPayIdempotencyError(KKPayError):
    """A webhook idempotency record is invalid or conflicts with stored data."""


class KKPayPaymentError(KKPayError):
    """A local payment record cannot be created or transitioned safely."""


class KKPayPaymentNotFoundError(KKPayPaymentError, LookupError):
    """No local payment record exists for the referenced merchant order."""


class KKPayOrderConflictError(KKPayPaymentError):
    """A local order or gateway trade identifier is already bound to another payment."""


class KKPayQRCodeError(KKPayError):
    """A payment QR image cannot be generated."""
