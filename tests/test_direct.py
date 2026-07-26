from __future__ import annotations

import time
from decimal import Decimal

import httpx
import pytest

from kkpay import (
    AsyncDirectPaymentService,
    AsyncTronClient,
    DirectPaymentService,
    FulfillmentState,
    OrderStatus,
    SQLitePaymentStore,
    TradeType,
    TronClient,
    payment_qr_payload,
)
from kkpay.tron import TRC20_TRANSFER_EVENT_TOPIC, USDT_TRC20_CONTRACT, tron_address_to_hex


RECEIVER = "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"
SENDER = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TX_HASH = "a" * 64


class DirectChainStub:
    def __init__(self, *, trade_type: str = TradeType.USDT_TRC20) -> None:
        self.trade_type = trade_type
        self.amount = Decimal("12.345")
        self.timestamp_ms = int(time.time() * 1000) + 20
        self.valid_detail = True
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        amount_sun = int(self.amount * Decimal(10**6))
        receiver_hex = tron_address_to_hex(RECEIVER)
        if path.endswith("/transactions/trc20"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        {
                            "transaction_id": TX_HASH,
                            "type": "Transfer",
                            "to": RECEIVER,
                            "from": SENDER,
                            "value": str(amount_sun),
                            "block_timestamp": self.timestamp_ms,
                            "token_info": {"address": USDT_TRC20_CONTRACT},
                        }
                    ],
                    "meta": {},
                },
            )
        if path.endswith("/transactions"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        {
                            "txID": TX_HASH,
                            "block_timestamp": self.timestamp_ms,
                            "ret": [{"contractRet": "SUCCESS"}],
                            "raw_data": {
                                "contract": [
                                    {
                                        "type": "TransferContract",
                                        "parameter": {
                                            "value": {
                                                "to_address": receiver_hex,
                                                "owner_address": tron_address_to_hex(SENDER),
                                                "amount": amount_sun,
                                            }
                                        },
                                    }
                                ]
                            },
                        }
                    ],
                    "meta": {},
                },
            )
        if path.endswith("/walletsolidity/gettransactioninfobyid"):
            if self.trade_type == TradeType.TRX:
                return httpx.Response(
                    200,
                    json={"id": TX_HASH, "blockTimeStamp": self.timestamp_ms},
                )
            return httpx.Response(
                200,
                json={
                    "id": TX_HASH,
                    "blockTimeStamp": self.timestamp_ms,
                    "receipt": {"result": "SUCCESS"},
                    "log": [
                        {
                            "address": tron_address_to_hex(USDT_TRC20_CONTRACT)[2:],
                            "topics": [
                                TRC20_TRANSFER_EVENT_TOPIC,
                                "0" * 64,
                                receiver_hex[2:].rjust(64, "0"),
                            ],
                            "data": f"{amount_sun if self.valid_detail else amount_sun + 1:064x}",
                        }
                    ],
                },
            )
        if path.endswith("/walletsolidity/gettransactionbyid"):
            return httpx.Response(
                200,
                json={
                    "txID": TX_HASH,
                    "ret": [{"contractRet": "SUCCESS"}],
                    "raw_data": {
                        "contract": [
                            {
                                "type": "TransferContract",
                                "parameter": {
                                    "value": {
                                        "to_address": receiver_hex,
                                        "owner_address": tron_address_to_hex(SENDER),
                                        "amount": amount_sun,
                                    }
                                },
                            }
                        ]
                    },
                },
            )
        raise AssertionError(f"unexpected direct-chain request: {request.method} {request.url}")


def sync_service(tmp_path, stub: DirectChainStub) -> DirectPaymentService:
    tron = TronClient("https://tron.example", transport=httpx.MockTransport(stub))
    return DirectPaymentService(RECEIVER, SQLitePaymentStore(tmp_path / "direct.sqlite"), tron_client=tron)


def test_direct_usdt_flow_needs_no_kk_gateway_and_fulfills_once(tmp_path):
    stub = DirectChainStub()
    service = sync_service(tmp_path, stub)
    payment = service.create_payment(order_id="DIRECT-1", amount="12.345", metadata={"user_id": 1})
    stub.amount = Decimal(payment.actual_amount)
    stub.timestamp_ms = int(payment.created_at * 1000) + 20

    calls = []
    first = service.poll_payment(
        payment.order_id,
        lambda stored, callback: calls.append((stored.order_id, callback.block_transaction_id)),
    )
    duplicate = service.poll_payment(payment.order_id, lambda *_: calls.append("duplicate"))

    assert payment.address == RECEIVER
    assert payment.payment_url == RECEIVER
    assert payment_qr_payload(payment) == RECEIVER
    assert first.handled and first.paid and first.transfer is not None
    assert first.payment.fulfillment_state is FulfillmentState.COMPLETED
    assert duplicate.duplicate and not duplicate.handled
    assert calls == [("DIRECT-1", TX_HASH)]
    assert all("6688" not in str(request.url) for request in stub.requests)
    assert all("create-transaction" not in request.url.path for request in stub.requests)


def test_direct_service_reserves_unique_amounts_and_can_price_cny(tmp_path):
    stub = DirectChainStub()
    service = sync_service(tmp_path, stub)

    first = service.create_payment(order_id="DIRECT-1", amount="10")
    second = service.create_payment(order_id="DIRECT-2", amount="10")
    priced = service.create_cny_payment(order_id="DIRECT-CNY", cny_amount="72", rate="7.2")

    assert first.actual_amount == "10"
    assert second.actual_amount == "10.000001"
    assert priced.amount == "72"
    assert priced.actual_amount == "10.000002"
    assert service.create_payment(order_id="DIRECT-1", amount="999") == first


def test_direct_trx_flow_verifies_confirmed_native_transfer(tmp_path):
    stub = DirectChainStub(trade_type=TradeType.TRX)
    service = sync_service(tmp_path, stub)
    payment = service.create_payment(
        order_id="DIRECT-TRX",
        amount="12.345",
        trade_type=TradeType.TRX,
    )
    stub.amount = Decimal(payment.actual_amount)
    stub.timestamp_ms = int(payment.created_at * 1000) + 20

    result = service.poll_payment(payment.order_id)

    assert result.handled
    assert result.payment.status is OrderStatus.PAID
    assert any(request.url.path.endswith("/transactions") for request in stub.requests)
    assert any(
        request.url.path.endswith("/walletsolidity/gettransactionbyid") for request in stub.requests
    )


def test_direct_service_never_credits_a_listed_transfer_without_detail_match(tmp_path):
    stub = DirectChainStub()
    stub.valid_detail = False
    service = sync_service(tmp_path, stub)
    payment = service.create_payment(order_id="DIRECT-INVALID", amount="12.345")
    stub.amount = Decimal(payment.actual_amount)
    stub.timestamp_ms = int(payment.created_at * 1000) + 20
    calls = []

    result = service.poll_payment(payment.order_id, lambda *_: calls.append("fulfilled"))

    assert not result.paid
    assert result.payment.status is OrderStatus.WAITING
    assert calls == []


@pytest.mark.asyncio
async def test_async_direct_service_uses_the_same_local_ledger(tmp_path):
    stub = DirectChainStub()
    tron = AsyncTronClient("https://tron.example", transport=httpx.MockTransport(stub))
    service = AsyncDirectPaymentService(
        RECEIVER,
        SQLitePaymentStore(tmp_path / "async-direct.sqlite"),
        tron_client=tron,
    )
    payment = await service.create_payment(order_id="ASYNC-DIRECT", amount="12.345")
    stub.amount = Decimal(payment.actual_amount)
    stub.timestamp_ms = int(payment.created_at * 1000) + 20
    calls = []

    async def fulfill(stored, callback):
        calls.append((stored.order_id, callback.block_transaction_id))

    result = await service.poll_payment(payment.order_id, fulfill)

    assert result.handled and result.paid
    assert calls == [("ASYNC-DIRECT", TX_HASH)]
