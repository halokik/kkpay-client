"""Minimal FastAPI integration skeleton.

Replace the three application-specific functions with real database code.
Secrets must come from the environment or an ignored local config file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from kkpay import AsyncKKPayClient, CallbackData, SQLiteIdempotencyStore


@dataclass
class LocalOrder:
    order_id: str
    trade_id: str
    amount: str
    actual_amount: str
    address: str


async def load_local_order(order_id: str) -> LocalOrder | None:
    """Load the bot's own order from its database."""
    raise NotImplementedError


async def mark_paid_and_fulfill_once(order: LocalOrder, callback: CallbackData) -> None:
    """Atomically mark paid and execute bot-specific fulfillment exactly once."""
    raise NotImplementedError


client = AsyncKKPayClient(
    base_url=os.getenv("KKPAY_BASE_URL", "http://127.0.0.1:6688"),
    merchant_id=os.environ["KKPAY_MERCHANT_ID"],
    api_token=os.environ["KKPAY_API_TOKEN"],
)
dedupe = SQLiteIdempotencyStore(
    os.getenv("KKPAY_IDEMPOTENCY_DB", "data/kkpay_webhooks.db")
)
app = FastAPI()


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/kkpay/notify")
async def kkpay_notify(request: Request) -> PlainTextResponse:
    payload = await request.json()
    callback = client.verify_callback(payload)
    local_order = await load_local_order(callback.order_id)
    if local_order is None:
        raise HTTPException(404, "unknown order")

    callback = client.verify_callback(
        payload,
        expected_order_id=local_order.order_id,
        expected_trade_id=local_order.trade_id,
        expected_amount=local_order.amount,
        expected_actual_amount=local_order.actual_amount,
        expected_address=local_order.address,
    )

    with dedupe.claim(callback) as claim:
        if not claim.acquired:
            if claim.completed:
                return PlainTextResponse("ok")
            raise HTTPException(409, "callback is processing")
        await mark_paid_and_fulfill_once(local_order, callback)

    return PlainTextResponse("ok")


@app.on_event("shutdown")
def close_resources() -> None:
    dedupe.close()
