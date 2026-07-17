"""Optional FastAPI webhook adapter for :mod:`kkpay.payments`.

Install the ``fastapi`` extra to use this module.  The adapter intentionally
returns only generic error bodies so callback payload details are not exposed
to the public internet.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import KKPayConfigurationError, KKPayError
from .payments import AsyncPaymentService, FulfillmentHandler, PaymentService


def create_fastapi_router(
    service: PaymentService | AsyncPaymentService,
    fulfill: FulfillmentHandler | None = None,
    *,
    path: str = "/kkpay/notify",
) -> Any:
    """Create a webhook router that returns the gateway-required ``ok`` body.

    A duplicate completed callback receives ``200 ok``.  A callback currently
    being fulfilled receives ``409`` and a failed fulfillment receives ``500``
    so the KK gateway can retry it later.
    """

    path = str(path or "").strip()
    if not path.startswith("/"):
        raise ValueError("webhook path must start with '/'")
    try:
        from fastapi import APIRouter, Request
        from fastapi.responses import PlainTextResponse
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise KKPayConfigurationError(
            "FastAPI support is unavailable; install kkpay-client[fastapi]"
        ) from exc

    # FastAPI resolves postponed annotations from module globals.  The optional
    # dependency is imported lazily above, so make the runtime request type
    # available without importing FastAPI whenever ``kkpay`` itself is imported.
    globals()["_FastAPIRequest"] = Request
    router = APIRouter()

    @router.post(path)
    async def kkpay_notify(request: _FastAPIRequest) -> Any:
        try:
            payload = await request.json()
        except Exception:
            return PlainTextResponse("invalid callback", status_code=400)
        if not isinstance(payload, Mapping):
            return PlainTextResponse("invalid callback", status_code=400)

        try:
            if isinstance(service, AsyncPaymentService):
                result = await service.process_callback(payload, fulfill)
            else:
                result = service.process_callback(payload, fulfill)
        except KKPayError:
            return PlainTextResponse("invalid callback", status_code=400)
        except Exception:
            return PlainTextResponse("processing failed", status_code=500)

        if result.retry_later:
            return PlainTextResponse("processing", status_code=409)
        return PlainTextResponse("ok")

    return router
