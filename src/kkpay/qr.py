"""Local QR-code helpers for KKPay checkout links.

The QR code deliberately contains the gateway ``payment_url`` rather than a
bare receiving address.  A checkout URL is network-aware, expires with the
order, and shows the exact amount before the user transfers funds.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any
from urllib.parse import urlparse

from .errors import KKPayQRCodeError


def payment_qr_payload(payment: Any) -> str:
    """Return the safe QR payload for an ``Order`` or local ``Payment``.

    The helper accepts either object so applications can render a QR code
    immediately after a raw client call or after persisting a payment through
    :class:`kkpay.PaymentService`.
    """

    value = str(getattr(payment, "payment_url", "") or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise KKPayQRCodeError("payment_url must be an absolute HTTP(S) URL")
    return value


def make_qr_png(
    payload: str,
    *,
    box_size: int = 8,
    border: int = 4,
    error_correction: str = "M",
) -> bytes:
    """Encode text into a PNG QR image.

    ``qrcode[pil]`` is installed with the SDK.  The delayed import keeps basic
    client and webhook use available in deliberately minimal deployments.
    """

    payload = str(payload or "").strip()
    if not payload:
        raise KKPayQRCodeError("QR payload must not be empty")
    if box_size < 1 or border < 0:
        raise KKPayQRCodeError("box_size must be positive and border cannot be negative")

    try:
        import qrcode
        from qrcode.constants import (
            ERROR_CORRECT_H,
            ERROR_CORRECT_L,
            ERROR_CORRECT_M,
            ERROR_CORRECT_Q,
        )
    except ImportError as exc:  # pragma: no cover - defensive for minimal installs
        raise KKPayQRCodeError(
            "QR support is unavailable; install kkpay-client with its QR dependency"
        ) from exc

    correction_map = {
        "L": ERROR_CORRECT_L,
        "M": ERROR_CORRECT_M,
        "Q": ERROR_CORRECT_Q,
        "H": ERROR_CORRECT_H,
    }
    level = correction_map.get(str(error_correction).upper())
    if level is None:
        raise KKPayQRCodeError("error_correction must be one of L, M, Q, H")

    try:
        code = qrcode.QRCode(
            version=None,
            error_correction=level,
            box_size=int(box_size),
            border=int(border),
        )
        code.add_data(payload)
        code.make(fit=True)
        image = code.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except KKPayQRCodeError:
        raise
    except Exception as exc:  # pragma: no cover - dependent-library failures
        raise KKPayQRCodeError(f"failed to render payment QR code: {exc}") from exc


def payment_qr_png(payment: Any, **kwargs: Any) -> bytes:
    """Render a checkout-link QR PNG for an ``Order`` or local ``Payment``."""

    return make_qr_png(payment_qr_payload(payment), **kwargs)
