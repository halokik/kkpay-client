import httpx
import pytest

from kkpay import AsyncKKPayClient, KKPayClient, KKPayConfigurationError, OrderStatus, make_signature


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("create-transaction"):
        return httpx.Response(
            200,
            json={
                "status_code": 200,
                "message": "success",
                "data": {
                    "trade_id": "T1",
                    "order_id": "O1",
                    "status": 1,
                    "amount": 100,
                    "actual_amount": "13.89",
                    "token": "TAddress",
                    "expiration_time": 1800,
                    "payment_url": "https://pay.example/T1",
                },
            },
        )
    if request.method == "GET":
        return httpx.Response(200, json={"trade_id": "T1", "status": 2, "trade_hash": "hash"})
    return httpx.Response(200, json={"status_code": 200, "data": {"trade_id": "T1"}})


def client():
    return KKPayClient("https://pay.example", "demo", "secret", transport=httpx.MockTransport(handler))


def test_public_http_is_secure_by_default():
    with pytest.raises(KKPayConfigurationError):
        KKPayClient("http://pay.example", "demo", "secret")


def test_sync_order_lifecycle():
    api = client()
    order = api.create_order(order_id="O1", amount=100, notify_url="https://bot.example/notify")
    assert order.actual_amount == "13.89"
    assert order.status is OrderStatus.WAITING
    assert api.query_order("T1").status is OrderStatus.PAID
    assert api.cancel_order("T1") == "T1"


def test_callback_verification():
    api = client()
    payload = {
        "trade_id": "T1",
        "order_id": "O1",
        "amount": 100,
        "actual_amount": "13.89",
        "token": "TAddress",
        "block_transaction_id": "hash",
        "status": 2,
    }
    payload["signature"] = make_signature(payload, "secret")
    callback = api.verify_callback(payload, expected_order_id="O1", expected_trade_id="T1")
    assert callback.status is OrderStatus.PAID


@pytest.mark.asyncio
async def test_async_order_lifecycle():
    api = AsyncKKPayClient(
        "https://pay.example", "demo", "secret", transport=httpx.MockTransport(handler)
    )
    order = await api.create_order(order_id="O1", amount=100, notify_url="https://bot.example/notify")
    assert order.trade_id == "T1"
    assert (await api.query_order("T1")).status is OrderStatus.PAID

