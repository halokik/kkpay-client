"""Standalone TRON collection example with no KK gateway dependency.

Set TRON_RECEIVER_ADDRESS to an address controlled by this deployment.  An
optional TRON_PRO_API_KEY belongs to this deployment and is only used to read
public confirmed chain data; never provide a wallet private key to this SDK.
"""

from __future__ import annotations

import os
from pathlib import Path

from kkpay import DirectPaymentService, SQLitePaymentStore, payment_qr_png


def grant_product_once(user_id: int, order_id: str) -> None:
    """Replace with an idempotent database transaction in the host application."""

    print(f"grant product to user={user_id} for order={order_id}")


def fulfill(payment, callback) -> None:
    grant_product_once(int(payment.metadata["user_id"]), payment.order_id)
    print(f"verified transaction: {callback.block_transaction_id}")


def main() -> None:
    receiver = os.environ["TRON_RECEIVER_ADDRESS"]
    payments = DirectPaymentService(
        receiver_address=receiver,
        store=SQLitePaymentStore("data/direct_payments.sqlite"),
        api_url=os.getenv("TRON_API_URL", "https://api.trongrid.io"),
        api_key=os.getenv("TRON_PRO_API_KEY") or None,
    )

    payment = payments.create_payment(
        order_id="DEMO_10001_1720000000",
        amount="13.89",  # USDT, or pass trade_type="tron.trx" for TRX
        metadata={"user_id": 10001},
    )
    Path("payment.png").write_bytes(payment_qr_png(payment))
    print(f"send {payment.actual_amount} to {payment.address} before {payment.expires_at}")

    # Call this periodically from your own scheduler or a "check payment" button.
    result = payments.poll_payment(payment.order_id, fulfill)
    print(f"paid={result.paid}, handled={result.handled}, duplicate={result.duplicate}")


if __name__ == "__main__":
    main()
