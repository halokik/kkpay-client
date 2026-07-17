import time

import pytest

from kkpay import (
    CallbackData,
    KKPayIdempotencyError,
    OrderStatus,
    SQLiteIdempotencyStore,
)


def callback(**updates):
    payload = {
        "trade_id": "T1",
        "order_id": "O1",
        "amount": 100,
        "actual_amount": "13.89",
        "token": "TAddress",
        "block_transaction_id": "hash",
        "status": 2,
        "signature": "signed-value",
    }
    payload.update(updates)
    return CallbackData(
        trade_id=str(payload["trade_id"]),
        order_id=str(payload["order_id"]),
        amount=payload["amount"],
        actual_amount=str(payload["actual_amount"]),
        token=str(payload["token"]),
        block_transaction_id=str(payload["block_transaction_id"]),
        status=OrderStatus.PAID,
        raw=payload,
    )


def test_completed_callback_is_not_acquired_twice():
    with SQLiteIdempotencyStore(":memory:") as store:
        first = store.claim(callback())
        assert first.acquired
        with first:
            pass
        assert store.get_state("T1:hash") == "completed"

        duplicate = store.claim(callback(signature="different-signature"))
        assert not duplicate.acquired
        assert duplicate.completed


def test_failed_callback_can_be_retried():
    with SQLiteIdempotencyStore(":memory:") as store:
        claim = store.claim(callback())
        claim.fail("temporary failure")
        assert store.get_state("T1:hash") == "failed"

        retry = store.claim(callback())
        assert retry.acquired
        assert retry.attempts == 2


def test_active_callback_is_not_claimed_concurrently():
    with SQLiteIdempotencyStore(":memory:") as store:
        first = store.claim(callback())
        second = store.claim(callback())
        assert first.acquired
        assert not second.acquired
        assert not second.completed


def test_same_key_with_changed_payload_is_rejected():
    with SQLiteIdempotencyStore(":memory:") as store:
        store.claim(callback())
        with pytest.raises(KKPayIdempotencyError, match="conflicts"):
            store.claim(callback(amount=999))


def test_stale_worker_cannot_finish_newer_claim():
    with SQLiteIdempotencyStore(":memory:", stale_after_seconds=0.01) as store:
        stale = store.claim(callback())
        time.sleep(0.02)
        current = store.claim(callback())
        assert current.acquired
        with pytest.raises(KKPayIdempotencyError, match="no longer active"):
            stale.complete()
        current.complete()
        assert store.get_state("T1:hash") == "completed"
