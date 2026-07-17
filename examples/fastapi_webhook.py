"""Complete FastAPI merchant-side KKPay integration.

Run with the ``fastapi`` extra installed.  Replace ``fulfill_once`` with a
real business-database transaction before exposing the callback endpoint.
Secrets must come from the environment or an ignored local config file.
"""

from __future__ import annotations

import os
import time

from fastapi import FastAPI

from kkpay import (
    AsyncKKPayClient,
    AsyncPaymentService,
    CallbackData,
    Payment,
    SQLitePaymentStore,
    create_fastapi_router,
    payment_qr_png,
)

client = AsyncKKPayClient(
    base_url=os.getenv("KKPAY_BASE_URL", "http://127.0.0.1:6688"),
    merchant_id=os.environ["KKPAY_MERCHANT_ID"],
    api_token=os.environ["KKPAY_API_TOKEN"],
)
store = SQLitePaymentStore(os.getenv("KKPAY_PAYMENTS_DB", "data/kkpay_payments.sqlite"))
payments = AsyncPaymentService(
    client,
    store,
    checkout_base_url=os.getenv("KKPAY_CHECKOUT_BASE_URL"),
)
app = FastAPI()


async def create_checkout(user_id: int, amount: int) -> tuple[Payment, bytes]:
    """Call this from a bot command or your own order-creation endpoint."""

    payment = await payments.create_payment(
        order_id=f"VIP_{user_id}_{int(time.time())}",
        amount=amount,
        notify_url="https://bot.example.com/kkpay/notify",
        redirect_url="https://t.me/example_bot",
        metadata={"user_id": user_id, "product": "vip-month"},
    )
    # Send these bytes as a Telegram photo or HTTP image response.  The QR
    # encodes the expiring payment_url rather than a bare wallet address.
    return payment, payment_qr_png(payment)


async def fulfill_once(payment: Payment, callback: CallbackData) -> None:
    """Atomically mark the business order paid and deliver it once.

    The SDK already owns the callback lease.  Your database must still use a
    transaction/unique constraint around the real membership, balance, or item
    delivery action.  Raising an exception causes the gateway callback to retry.
    """

    raise NotImplementedError("replace fulfill_once with the application's business transaction")


app.include_router(create_fastapi_router(payments, fulfill_once))


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.on_event("shutdown")
def close_resources() -> None:
    store.close()
