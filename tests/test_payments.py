import json

import httpx
import pytest

from kkpay import (
    AsyncKKPayClient,
    AsyncPaymentService,
    FulfillmentState,
    KKPayCallbackError,
    KKPayClient,
    OrderStatus,
    PaymentService,
    SQLitePaymentStore,
    create_fastapi_router,
    make_signature,
)


class GatewayStub:
    def __init__(self) -> None:
        self.create_calls = 0
        self.cancel_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("create-transaction"):
            self.create_calls += 1
            body = json.loads(request.content)
            order_id = body["order_id"]
            return httpx.Response(
                200,
                json={
                    "status_code": 200,
                    "data": {
                        "trade_id": f"T-{order_id}",
                        "order_id": order_id,
                        "status": 1,
                        "amount": body["amount"],
                        "actual_amount": "13.89",
                        "token": "TReceivingAddress",
                        "expiration_time": 1800,
                        "payment_url": f"https://pay.example/checkout/T-{order_id}",
                    },
                },
            )
        if request.url.path.startswith("/pay/check-status/"):
            trade_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"trade_id": trade_id, "status": 2, "trade_hash": "hash"})
        if request.url.path.endswith("cancel-transaction"):
            self.cancel_calls += 1
            body = json.loads(request.content)
            return httpx.Response(200, json={"status_code": 200, "data": {"trade_id": body["trade_id"]}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


def sync_service(tmp_path, gateway: GatewayStub) -> PaymentService:
    client = KKPayClient(
        "https://pay.example",
        "merchant",
        "secret",
        transport=httpx.MockTransport(gateway),
    )
    return PaymentService(client, SQLitePaymentStore(tmp_path / "payments.sqlite"))


def callback_for(payment, **updates):
    payload = {
        "trade_id": payment.trade_id,
        "order_id": payment.order_id,
        "amount": payment.amount,
        "actual_amount": payment.actual_amount,
        "token": payment.address,
        "block_transaction_id": "block-hash-1",
        "status": 2,
    }
    payload.update(updates)
    payload["signature"] = make_signature(payload, "secret")
    return payload


def create_payment(service: PaymentService, order_id: str = "ORDER-1"):
    return service.create_payment(
        order_id=order_id,
        amount="100.00",
        notify_url="https://bot.example/kkpay/notify",
        redirect_url="https://t.me/example_bot",
        metadata={"user_id": 10001},
    )


def test_create_payment_persists_checkout_and_reuses_local_order(tmp_path):
    gateway = GatewayStub()
    service = sync_service(tmp_path, gateway)

    first = create_payment(service)
    second = create_payment(service)

    assert gateway.create_calls == 1
    assert first.trade_id == "T-ORDER-1"
    assert first.amount == "100"
    assert first.actual_amount == "13.89"
    assert first.qr_payload == "https://pay.example/checkout/T-ORDER-1"
    assert first.metadata == {"user_id": 10001}
    assert second == first
    assert service.query_payment(first.order_id).status is OrderStatus.PAID


def test_public_checkout_base_rewrites_a_locally_created_gateway_url(tmp_path):
    gateway = GatewayStub()
    client = KKPayClient(
        "http://127.0.0.1:6688",
        "merchant",
        "secret",
        transport=httpx.MockTransport(gateway),
    )
    service = PaymentService(
        client,
        SQLitePaymentStore(tmp_path / "payments.sqlite"),
        checkout_base_url="https://pay.example/gateway",
    )

    payment = create_payment(service)

    assert payment.payment_url == "https://pay.example/gateway/checkout/T-ORDER-1"
    assert payment.qr_payload == payment.payment_url


def test_signed_callback_runs_fulfillment_once_and_deduplicates(tmp_path):
    gateway = GatewayStub()
    service = sync_service(tmp_path, gateway)
    payment = create_payment(service)
    calls = []

    first = service.process_callback(
        callback_for(payment),
        lambda stored, callback: calls.append((stored.order_id, callback.block_transaction_id)),
    )
    duplicate = service.process_callback(callback_for(payment), lambda *_: calls.append("duplicate"))

    assert first.handled and not first.duplicate and not first.retry_later
    assert duplicate.duplicate and not duplicate.retry_later
    assert calls == [("ORDER-1", "block-hash-1")]
    stored = service.require_payment(payment.order_id)
    assert stored.status is OrderStatus.PAID
    assert stored.fulfillment_state is FulfillmentState.COMPLETED
    assert stored.block_transaction_id == "block-hash-1"


def test_failed_fulfillment_is_released_for_gateway_retry(tmp_path):
    gateway = GatewayStub()
    service = sync_service(tmp_path, gateway)
    payment = create_payment(service)
    payload = callback_for(payment)

    with pytest.raises(RuntimeError, match="temporary"):
        service.process_callback(payload, lambda *_: (_ for _ in ()).throw(RuntimeError("temporary")))
    assert service.require_payment(payment.order_id).fulfillment_state is FulfillmentState.FAILED

    result = service.process_callback(payload, lambda *_: None)
    assert result.handled
    assert result.payment.fulfillment_attempts == 2
    assert result.payment.fulfillment_state is FulfillmentState.COMPLETED


def test_active_claim_requests_retry_until_original_worker_finishes(tmp_path):
    gateway = GatewayStub()
    service = sync_service(tmp_path, gateway)
    payment = create_payment(service)
    payload = callback_for(payment)

    first_claim = service.claim_callback(payload)
    assert first_claim.acquired
    waiting = service.process_callback(payload)
    assert waiting.retry_later and not waiting.duplicate

    first_claim.complete()
    duplicate = service.process_callback(payload)
    assert duplicate.duplicate and not duplicate.retry_later


def test_callback_must_match_the_persisted_order_values(tmp_path):
    gateway = GatewayStub()
    service = sync_service(tmp_path, gateway)
    payment = create_payment(service)
    payload = callback_for(payment, actual_amount="13.90")

    with pytest.raises(KKPayCallbackError, match="actual_amount"):
        service.process_callback(payload)


def test_cancel_updates_the_local_ledger_after_gateway_success(tmp_path):
    gateway = GatewayStub()
    service = sync_service(tmp_path, gateway)
    payment = create_payment(service)

    cancelled = service.cancel_payment(payment.order_id)
    repeated = service.cancel_payment(payment.order_id)

    assert gateway.cancel_calls == 1
    assert cancelled.status is OrderStatus.CANCELLED
    assert repeated == cancelled


@pytest.mark.asyncio
async def test_async_service_supports_async_fulfillment(tmp_path):
    gateway = GatewayStub()
    client = AsyncKKPayClient(
        "https://pay.example",
        "merchant",
        "secret",
        transport=httpx.MockTransport(gateway),
    )
    service = AsyncPaymentService(client, SQLitePaymentStore(tmp_path / "async-payments.sqlite"))
    payment = await service.create_payment(
        order_id="ASYNC-1",
        amount=100,
        notify_url="https://bot.example/notify",
        redirect_url="https://t.me/example_bot",
    )
    calls = []

    async def fulfill(stored, callback):
        calls.append((stored.order_id, callback.trade_id))

    result = await service.process_callback(callback_for(payment), fulfill)
    assert result.handled
    assert calls == [("ASYNC-1", "T-ASYNC-1")]


def test_fastapi_router_returns_ok_and_deduplicates_completed_callback(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    gateway = GatewayStub()
    service = sync_service(tmp_path, gateway)
    payment = create_payment(service)
    calls = []
    app = FastAPI()
    app.include_router(create_fastapi_router(service, lambda *_: calls.append("fulfilled")))
    test_client = TestClient(app)

    first = test_client.post("/kkpay/notify", json=callback_for(payment))
    duplicate = test_client.post("/kkpay/notify", json=callback_for(payment))
    invalid = test_client.post("/kkpay/notify", json={"order_id": payment.order_id})

    assert first.status_code == 200 and first.text == "ok"
    assert duplicate.status_code == 200 and duplicate.text == "ok"
    assert invalid.status_code == 400
    assert calls == ["fulfilled"]
