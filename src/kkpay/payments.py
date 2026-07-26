"""Merchant-side payment orchestration for gateway and direct-chain payments.

This module intentionally owns the application-facing payment lifecycle:
creating a local record, rendering the checkout payload, validating a signed
callback against that record, and claiming fulfillment exactly once.  The
legacy services use a KKPay-compatible gateway; :mod:`kkpay.direct` instead
verifies confirmed TRON transfers directly and never contacts that gateway.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .client import AsyncKKPayClient, KKPayClient
from .errors import (
    KKPayOrderConflictError,
    KKPayPaymentError,
    KKPayPaymentNotFoundError,
)
from .models import CallbackData, Order, OrderStatus, QueryResult, TradeType


class FulfillmentState(str, Enum):
    """State of the merchant's exactly-once fulfillment attempt."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


def _amount_text(value: Any) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise KKPayPaymentError("payment amount is not numeric") from exc
    if not amount.is_finite() or amount <= 0:
        raise KKPayPaymentError("payment amount must be positive")
    normalized = amount.normalize()
    return format(normalized, "f")


def _amounts_equal(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Payment:
    """A persisted payment intent from a gateway or direct TRON service.

    A status of ``PAID`` becomes safe to fulfill only through a verified source
    (a signed gateway callback or a direct confirmed-chain verification) and a
    successful ``PaymentClaim``.  A direct service may use polling as that
    verified source; a legacy gateway service must not fulfill merely from an
    unsigned status query.
    """

    order_id: str
    trade_id: str
    amount: str
    actual_amount: str
    address: str
    trade_type: str
    payment_url: str
    status: OrderStatus
    created_at: float
    updated_at: float
    expires_at: float | None = None
    paid_at: float | None = None
    block_transaction_id: str = ""
    fulfillment_state: FulfillmentState = FulfillmentState.PENDING
    fulfillment_attempts: int = 0
    metadata: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_terminal(self) -> bool:
        return self.status in {OrderStatus.PAID, OrderStatus.EXPIRED, OrderStatus.CANCELLED}

    @property
    def is_expired_locally(self) -> bool:
        return self.expires_at is not None and time.time() >= self.expires_at

    @property
    def qr_payload(self) -> str:
        """The checkout URL that should be encoded into a QR image."""

        return self.payment_url


@dataclass(frozen=True)
class WebhookResult:
    """Outcome of processing one signed gateway callback."""

    payment: Payment
    callback: CallbackData
    handled: bool
    duplicate: bool
    retry_later: bool


FulfillmentHandler = Callable[[Payment, CallbackData], Any]


class PaymentClaim:
    """An atomic lease to run merchant fulfillment once for a paid callback."""

    def __init__(
        self,
        store: "SQLitePaymentStore",
        payment: Payment,
        callback: CallbackData,
        claim_id: str,
        *,
        acquired: bool,
        completed: bool,
        attempts: int,
    ) -> None:
        self._store = store
        self.payment = payment
        self.callback = callback
        self._claim_id = claim_id
        self.acquired = acquired
        self.completed = completed
        self.attempts = attempts
        self._finished = False

    def complete(self) -> Payment:
        """Persist a successful fulfillment for this callback lease."""

        if not self.acquired or self._finished:
            return self.payment
        self.payment = self._store._finish_claim(
            self.payment.order_id,
            self._claim_id,
            FulfillmentState.COMPLETED,
            "",
        )
        self.completed = True
        self._finished = True
        return self.payment

    def fail(self, error: object = "") -> Payment:
        """Release a failed fulfillment so a gateway retry can claim it."""

        if not self.acquired or self._finished:
            return self.payment
        self.payment = self._store._finish_claim(
            self.payment.order_id,
            self._claim_id,
            FulfillmentState.FAILED,
            str(error),
        )
        self._finished = True
        return self.payment

    def __enter__(self) -> "PaymentClaim":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc is None:
            self.complete()
        else:
            self.fail(exc)
        return False


class SQLitePaymentStore:
    """A durable local payment ledger with callback-safe fulfillment leases.

    Keep this database private to one merchant application.  It stores order
    data and application metadata but never stores a merchant API token or a
    wallet private key.
    """

    def __init__(self, path: str | Path, *, stale_after_seconds: float = 300.0) -> None:
        raw_path = str(path)
        if not raw_path:
            raise ValueError("payment database path must not be empty")
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        if raw_path != ":memory:":
            expanded = Path(raw_path).expanduser()
            expanded.resolve().parent.mkdir(parents=True, exist_ok=True)
            raw_path = str(expanded)
        self.path = raw_path
        self.stale_after_seconds = float(stale_after_seconds)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def __repr__(self) -> str:
        return f"SQLitePaymentStore(path={self.path!r})"

    def _initialize(self) -> None:
        with self._lock:
            if self.path != ":memory:":
                self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA busy_timeout=10000")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS kkpay_payments (
                    order_id TEXT PRIMARY KEY,
                    trade_id TEXT NOT NULL UNIQUE,
                    amount TEXT NOT NULL,
                    actual_amount TEXT NOT NULL,
                    address TEXT NOT NULL,
                    trade_type TEXT NOT NULL,
                    payment_url TEXT NOT NULL,
                    gateway_status INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL,
                    paid_at REAL,
                    block_transaction_id TEXT NOT NULL DEFAULT '',
                    fulfillment_state TEXT NOT NULL DEFAULT 'pending',
                    fulfillment_attempts INTEGER NOT NULL DEFAULT 0,
                    claim_id TEXT NOT NULL DEFAULT '',
                    locked_at REAL,
                    last_error TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_kkpay_payments_status "
                "ON kkpay_payments(gateway_status, updated_at)"
            )

    @staticmethod
    def _metadata_json(metadata: Mapping[str, Any] | None) -> str:
        if metadata is None:
            return "{}"
        if not isinstance(metadata, Mapping):
            raise KKPayPaymentError("payment metadata must be a mapping")
        try:
            return json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError) as exc:
            raise KKPayPaymentError("payment metadata cannot be serialized") from exc

    @staticmethod
    def _row_to_payment(row: sqlite3.Row) -> Payment:
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        try:
            state = FulfillmentState(str(row["fulfillment_state"] or FulfillmentState.PENDING.value))
            status = OrderStatus.parse(row["gateway_status"])
        except (TypeError, ValueError) as exc:
            raise KKPayPaymentError("stored payment has an invalid state") from exc
        return Payment(
            order_id=str(row["order_id"]),
            trade_id=str(row["trade_id"]),
            amount=str(row["amount"]),
            actual_amount=str(row["actual_amount"]),
            address=str(row["address"]),
            trade_type=str(row["trade_type"]),
            payment_url=str(row["payment_url"]),
            status=status,
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            expires_at=float(row["expires_at"]) if row["expires_at"] is not None else None,
            paid_at=float(row["paid_at"]) if row["paid_at"] is not None else None,
            block_transaction_id=str(row["block_transaction_id"] or ""),
            fulfillment_state=state,
            fulfillment_attempts=int(row["fulfillment_attempts"] or 0),
            metadata=metadata,
        )

    def _row_for_order(self, order_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM kkpay_payments WHERE order_id = ?", (order_id,)
        ).fetchone()

    def create_payment(
        self,
        order: Order,
        *,
        trade_type: str = TradeType.USDT_TRC20,
        metadata: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> Payment:
        """Persist a newly-created gateway order before it is shown to a user."""

        timestamp = time.time() if now is None else float(now)
        timeout = max(0, int(order.expiration_time or 0))
        expires_at = timestamp + timeout if timeout else None
        record = (
            order.order_id,
            order.trade_id,
            _amount_text(order.amount),
            _amount_text(order.actual_amount),
            order.address,
            str(trade_type or TradeType.USDT_TRC20),
            order.payment_url,
            int(order.status),
            timestamp,
            timestamp,
            expires_at,
            self._metadata_json(metadata),
        )
        with self._lock:
            try:
                self._connection.execute(
                    """
                    INSERT INTO kkpay_payments (
                        order_id, trade_id, amount, actual_amount, address, trade_type,
                        payment_url, gateway_status, created_at, updated_at, expires_at,
                        metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    record,
                )
            except sqlite3.IntegrityError as exc:
                raise KKPayOrderConflictError(
                    "a payment with this order_id or trade_id already exists"
                ) from exc
            row = self._row_for_order(order.order_id)
        if row is None:  # pragma: no cover - SQLite invariant guard
            raise KKPayPaymentError("payment was not saved")
        return self._row_to_payment(row)

    def create_direct_payment(
        self,
        order: Order,
        *,
        trade_type: str = TradeType.USDT_TRC20,
        metadata: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> Payment:
        """Persist a direct-chain payment and atomically reserve its amount.

        A self-hosted collector may use one receiving address for several
        active orders.  Exact on-chain amounts are therefore reserved while an
        order is waiting, so a single transfer cannot satisfy two open orders.
        Expired, cancelled, and paid orders no longer reserve their amount.
        """

        timestamp = time.time() if now is None else float(now)
        timeout = max(0, int(order.expiration_time or 0))
        expires_at = timestamp + timeout if timeout else None
        normalized_trade_type = str(trade_type or TradeType.USDT_TRC20)
        actual_amount = _amount_text(order.actual_amount)
        record = (
            order.order_id,
            order.trade_id,
            _amount_text(order.amount),
            actual_amount,
            order.address,
            normalized_trade_type,
            order.payment_url,
            int(order.status),
            timestamp,
            timestamp,
            expires_at,
            self._metadata_json(metadata),
        )
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                reserved = self._connection.execute(
                    """
                    SELECT order_id FROM kkpay_payments
                    WHERE address = ? AND trade_type = ? AND actual_amount = ?
                      AND gateway_status = ?
                      AND (expires_at IS NULL OR expires_at > ?)
                    LIMIT 1
                    """,
                    (
                        order.address,
                        normalized_trade_type,
                        actual_amount,
                        int(OrderStatus.WAITING),
                        timestamp,
                    ),
                ).fetchone()
                if reserved is not None:
                    raise KKPayOrderConflictError(
                        "actual_amount is already reserved by an active direct payment"
                    )
                self._connection.execute(
                    """
                    INSERT INTO kkpay_payments (
                        order_id, trade_id, amount, actual_amount, address, trade_type,
                        payment_url, gateway_status, created_at, updated_at, expires_at,
                        metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    record,
                )
                row = self._row_for_order(order.order_id)
                self._connection.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise KKPayOrderConflictError(
                    "a payment with this order_id or trade_id already exists"
                ) from exc
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        if row is None:  # pragma: no cover - SQLite invariant guard
            raise KKPayPaymentError("payment was not saved")
        return self._row_to_payment(row)

    def get_by_order_id(self, order_id: str) -> Payment | None:
        """Return a local payment by merchant order identifier."""

        order_id = str(order_id or "").strip()
        if not order_id:
            return None
        with self._lock:
            row = self._row_for_order(order_id)
        return self._row_to_payment(row) if row is not None else None

    def get_by_trade_id(self, trade_id: str) -> Payment | None:
        """Return a local payment by the gateway trade identifier."""

        trade_id = str(trade_id or "").strip()
        if not trade_id:
            return None
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM kkpay_payments WHERE trade_id = ?", (trade_id,)
            ).fetchone()
        return self._row_to_payment(row) if row is not None else None

    def list_open_payments(self, *, limit: int = 100) -> list[Payment]:
        """Return waiting or paid records that may still need local handling."""

        safe_limit = int(limit)
        if safe_limit < 1 or safe_limit > 10000:
            raise ValueError("limit must be between 1 and 10000")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM kkpay_payments
                WHERE gateway_status IN (?, ?)
                ORDER BY created_at ASC, order_id ASC
                LIMIT ?
                """,
                (int(OrderStatus.WAITING), int(OrderStatus.PAID), safe_limit),
            ).fetchall()
        return [self._row_to_payment(row) for row in rows]

    def mark_cancelled(self, order_id: str) -> Payment:
        """Record a successful gateway cancellation without touching paid orders."""

        order_id = str(order_id or "").strip()
        if not order_id:
            raise KKPayPaymentNotFoundError("order_id must not be empty")
        now = time.time()
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._row_for_order(order_id)
                if row is None:
                    raise KKPayPaymentNotFoundError("local payment was not found")
                payment = self._row_to_payment(row)
                if payment.status is OrderStatus.PAID:
                    raise KKPayPaymentError("a paid payment cannot be cancelled")
                if payment.status is not OrderStatus.CANCELLED:
                    self._connection.execute(
                        "UPDATE kkpay_payments SET gateway_status = ?, updated_at = ? "
                        "WHERE order_id = ?",
                        (int(OrderStatus.CANCELLED), now, order_id),
                    )
                row = self._row_for_order(order_id)
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        if row is None:  # pragma: no cover - transaction invariant guard
            raise KKPayPaymentNotFoundError("local payment was not found")
        return self._row_to_payment(row)

    def expire_if_due(self, order_id: str, *, now: float | None = None) -> Payment:
        """Expire one still-waiting record after its local timeout.

        Direct-chain callers should first scan the full order time window, then
        call this method only if no matching confirmed transfer was found.
        """

        order_id = str(order_id or "").strip()
        if not order_id:
            raise KKPayPaymentNotFoundError("order_id must not be empty")
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._row_for_order(order_id)
                if row is None:
                    raise KKPayPaymentNotFoundError("local payment was not found")
                payment = self._row_to_payment(row)
                if (
                    payment.status is OrderStatus.WAITING
                    and payment.expires_at is not None
                    and payment.expires_at <= timestamp
                ):
                    self._connection.execute(
                        "UPDATE kkpay_payments SET gateway_status = ?, updated_at = ? "
                        "WHERE order_id = ?",
                        (int(OrderStatus.EXPIRED), timestamp, order_id),
                    )
                row = self._row_for_order(order_id)
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        if row is None:  # pragma: no cover - transaction invariant guard
            raise KKPayPaymentNotFoundError("local payment was not found")
        return self._row_to_payment(row)

    @staticmethod
    def _validate_callback(payment: Payment, callback: CallbackData) -> None:
        if callback.status is not OrderStatus.PAID:
            raise KKPayPaymentError("only paid callbacks can be fulfilled")
        if callback.trade_id != payment.trade_id:
            raise KKPayPaymentError("callback trade_id does not match local payment")
        if callback.order_id != payment.order_id:
            raise KKPayPaymentError("callback order_id does not match local payment")
        if not _amounts_equal(callback.amount, payment.amount):
            raise KKPayPaymentError("callback amount does not match local payment")
        if not _amounts_equal(callback.actual_amount, payment.actual_amount):
            raise KKPayPaymentError("callback actual_amount does not match local payment")
        if callback.address != payment.address:
            raise KKPayPaymentError("callback receiving address does not match local payment")
        if (
            payment.block_transaction_id
            and callback.block_transaction_id
            and payment.block_transaction_id != callback.block_transaction_id
        ):
            raise KKPayPaymentError("callback blockchain transaction conflicts with local payment")

    def claim_callback(self, callback: CallbackData) -> PaymentClaim:
        """Atomically lease an already-verified paid callback for fulfillment.

        A completed callback returns ``acquired=False, completed=True`` and can
        safely receive an ``ok`` webhook response.  A fresh in-progress lease
        returns ``acquired=False, completed=False`` so the gateway should retry.
        """

        now = time.time()
        stale_before = now - self.stale_after_seconds
        claim_id = uuid.uuid4().hex
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._row_for_order(callback.order_id)
                if row is None:
                    raise KKPayPaymentNotFoundError("callback references an unknown local order")
                payment = self._row_to_payment(row)
                self._validate_callback(payment, callback)
                if payment.status in {OrderStatus.EXPIRED, OrderStatus.CANCELLED}:
                    raise KKPayPaymentError("callback targets an expired or cancelled payment")

                if payment.fulfillment_state is FulfillmentState.COMPLETED:
                    acquired = False
                    completed = True
                    attempts = payment.fulfillment_attempts
                    current = payment
                elif (
                    payment.fulfillment_state is FulfillmentState.PROCESSING
                    and self._locked_at(payment.order_id) > stale_before
                ):
                    acquired = False
                    completed = False
                    attempts = payment.fulfillment_attempts
                    current = payment
                else:
                    attempts = payment.fulfillment_attempts + 1
                    block_hash = callback.block_transaction_id or payment.block_transaction_id
                    self._connection.execute(
                        """
                        UPDATE kkpay_payments
                        SET gateway_status = ?, paid_at = COALESCE(paid_at, ?),
                            block_transaction_id = ?, fulfillment_state = ?,
                            fulfillment_attempts = ?, claim_id = ?, locked_at = ?,
                            last_error = '', updated_at = ?
                        WHERE order_id = ?
                        """,
                        (
                            int(OrderStatus.PAID),
                            now,
                            block_hash,
                            FulfillmentState.PROCESSING.value,
                            attempts,
                            claim_id,
                            now,
                            now,
                            payment.order_id,
                        ),
                    )
                    updated = self._row_for_order(payment.order_id)
                    if updated is None:  # pragma: no cover - transaction invariant guard
                        raise KKPayPaymentNotFoundError("local payment disappeared")
                    current = self._row_to_payment(updated)
                    acquired = True
                    completed = False
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

        return PaymentClaim(
            self,
            current,
            callback,
            claim_id,
            acquired=acquired,
            completed=completed,
            attempts=attempts,
        )

    def _locked_at(self, order_id: str) -> float:
        row = self._connection.execute(
            "SELECT locked_at FROM kkpay_payments WHERE order_id = ?", (order_id,)
        ).fetchone()
        return float(row["locked_at"] or 0) if row is not None else 0.0

    def _finish_claim(
        self,
        order_id: str,
        claim_id: str,
        state: FulfillmentState,
        error: str,
    ) -> Payment:
        if state not in {FulfillmentState.COMPLETED, FulfillmentState.FAILED}:
            raise KKPayPaymentError("invalid fulfillment completion state")
        now = time.time()
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE kkpay_payments
                SET fulfillment_state = ?, claim_id = '', locked_at = NULL,
                    last_error = ?, updated_at = ?
                WHERE order_id = ? AND claim_id = ? AND fulfillment_state = ?
                """,
                (
                    state.value,
                    str(error)[:500],
                    now,
                    order_id,
                    claim_id,
                    FulfillmentState.PROCESSING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise KKPayPaymentError("payment fulfillment claim is no longer active")
            row = self._row_for_order(order_id)
        if row is None:  # pragma: no cover - SQLite invariant guard
            raise KKPayPaymentNotFoundError("local payment was not found")
        return self._row_to_payment(row)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "SQLitePaymentStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.close()
        return False


class _PaymentServiceBase:
    def __init__(
        self,
        client: KKPayClient | AsyncKKPayClient,
        store: SQLitePaymentStore,
        *,
        checkout_base_url: str | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.checkout_base_url = self._normalize_checkout_base_url(checkout_base_url)

    @staticmethod
    def _normalize_checkout_base_url(value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        parsed = urlsplit(str(value).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise KKPayPaymentError("checkout_base_url must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment:
            raise KKPayPaymentError("checkout_base_url cannot contain a query or fragment")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))

    @staticmethod
    def _order_id(value: str) -> str:
        order_id = str(value or "").strip()
        if not order_id:
            raise ValueError("order_id must not be empty")
        return order_id

    def get_payment(self, order_id: str) -> Payment | None:
        return self.store.get_by_order_id(order_id)

    def require_payment(self, order_id: str) -> Payment:
        payment = self.get_payment(order_id)
        if payment is None:
            raise KKPayPaymentNotFoundError("local payment was not found")
        return payment

    def claim_callback(self, payload: Mapping[str, Any]) -> PaymentClaim:
        """Verify a callback against the ledger and obtain a fulfillment lease."""

        if not isinstance(payload, Mapping):
            raise KKPayPaymentError("callback payload must be a JSON object")
        order_id = self._order_id(str(payload.get("order_id") or ""))
        payment = self.require_payment(order_id)
        callback = self.client.verify_callback(
            payload,
            expected_order_id=payment.order_id,
            expected_trade_id=payment.trade_id,
            expected_amount=payment.amount,
            expected_actual_amount=payment.actual_amount,
            expected_address=payment.address,
        )
        return self.store.claim_callback(callback)

    @staticmethod
    def _result_from_unclaimed(claim: PaymentClaim) -> WebhookResult:
        return WebhookResult(
            payment=claim.payment,
            callback=claim.callback,
            handled=False,
            duplicate=claim.completed,
            retry_later=not claim.completed,
        )

    def _store_order(
        self,
        requested_order_id: str,
        order: Order,
        trade_type: str,
        metadata: Mapping[str, Any] | None,
    ) -> Payment:
        if order.order_id != requested_order_id:
            raise KKPayPaymentError("gateway returned an unexpected order_id")
        order = self._public_checkout_order(order)
        try:
            return self.store.create_payment(order, trade_type=trade_type, metadata=metadata)
        except KKPayOrderConflictError:
            existing = self.store.get_by_order_id(requested_order_id)
            if existing is not None and existing.trade_id == order.trade_id:
                return existing
            raise

    def _public_checkout_order(self, order: Order) -> Order:
        """Rewrite a loopback-created checkout URL to a configured public host.

        The live KK gateway derives ``payment_url`` from the incoming HTTP Host.
        Merchant apps commonly call it through ``127.0.0.1`` but must show users
        a public domain; this explicit trusted configuration avoids leaking a
        loopback URL into a QR code.
        """

        if self.checkout_base_url is None:
            return order
        source = urlsplit(order.payment_url)
        if source.scheme not in {"http", "https"} or not source.netloc or not source.path.startswith("/"):
            raise KKPayPaymentError("gateway returned an invalid checkout payment_url")
        base = urlsplit(self.checkout_base_url)
        public_url = urlunsplit(
            (
                base.scheme,
                base.netloc,
                f"{base.path.rstrip('/')}{source.path}",
                source.query,
                "",
            )
        )
        raw = dict(order.raw)
        raw["payment_url"] = public_url
        return replace(order, payment_url=public_url, raw=raw)

    @staticmethod
    def _assert_cancellable(payment: Payment) -> None:
        if payment.status in {OrderStatus.PAID, OrderStatus.EXPIRED} or payment.fulfillment_state in {
            FulfillmentState.PROCESSING,
            FulfillmentState.COMPLETED,
        }:
            raise KKPayPaymentError("a paid or expired payment cannot be cancelled")


class PaymentService(_PaymentServiceBase):
    """Synchronous complete merchant-side payment workflow."""

    client: KKPayClient

    def create_payment(
        self,
        *,
        order_id: str,
        amount: Any,
        notify_url: str,
        redirect_url: str,
        trade_type: str = TradeType.USDT_TRC20,
        timeout: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Payment:
        """Create, persist, and return a checkout-ready USDT/TRX payment."""

        requested_order_id = self._order_id(order_id)
        existing = self.store.get_by_order_id(requested_order_id)
        if existing is not None:
            return existing
        kwargs: dict[str, Any] = {
            "order_id": requested_order_id,
            "amount": amount,
            "notify_url": notify_url,
            "redirect_url": redirect_url,
            "trade_type": str(trade_type),
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        order = self.client.create_order(**kwargs)
        return self._store_order(requested_order_id, order, str(trade_type), metadata)

    def query_payment(self, order_id: str) -> QueryResult:
        """Query the gateway for UI/reconciliation, never for direct fulfillment."""

        payment = self.require_payment(order_id)
        return self.client.query_order(payment.trade_id)

    def cancel_payment(self, order_id: str) -> Payment:
        """Cancel a waiting payment at the gateway and in the local ledger."""

        payment = self.require_payment(order_id)
        if payment.status is OrderStatus.CANCELLED:
            return payment
        self._assert_cancellable(payment)
        self.client.cancel_order(payment.trade_id)
        return self.store.mark_cancelled(payment.order_id)

    def process_callback(
        self,
        payload: Mapping[str, Any],
        fulfill: FulfillmentHandler | None = None,
    ) -> WebhookResult:
        """Verify, claim, and process one callback exactly once.

        If another worker already owns a fresh claim, ``retry_later`` is true;
        webhook adapters should return a non-2xx response so the gateway retries.
        """

        claim = self.claim_callback(payload)
        if not claim.acquired:
            return self._result_from_unclaimed(claim)
        try:
            if fulfill is not None:
                result = fulfill(claim.payment, claim.callback)
                if inspect.isawaitable(result):
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()
                    raise TypeError("async fulfillment requires AsyncPaymentService")
        except BaseException as exc:
            claim.fail(exc)
            raise
        payment = claim.complete()
        return WebhookResult(
            payment=payment,
            callback=claim.callback,
            handled=True,
            duplicate=False,
            retry_later=False,
        )


class AsyncPaymentService(_PaymentServiceBase):
    """Asyncio-native merchant-side payment workflow for async bots and ASGI."""

    client: AsyncKKPayClient

    async def create_payment(
        self,
        *,
        order_id: str,
        amount: Any,
        notify_url: str,
        redirect_url: str,
        trade_type: str = TradeType.USDT_TRC20,
        timeout: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Payment:
        requested_order_id = self._order_id(order_id)
        existing = self.store.get_by_order_id(requested_order_id)
        if existing is not None:
            return existing
        kwargs: dict[str, Any] = {
            "order_id": requested_order_id,
            "amount": amount,
            "notify_url": notify_url,
            "redirect_url": redirect_url,
            "trade_type": str(trade_type),
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        order = await self.client.create_order(**kwargs)
        return self._store_order(requested_order_id, order, str(trade_type), metadata)

    async def query_payment(self, order_id: str) -> QueryResult:
        payment = self.require_payment(order_id)
        return await self.client.query_order(payment.trade_id)

    async def cancel_payment(self, order_id: str) -> Payment:
        payment = self.require_payment(order_id)
        if payment.status is OrderStatus.CANCELLED:
            return payment
        self._assert_cancellable(payment)
        await self.client.cancel_order(payment.trade_id)
        return self.store.mark_cancelled(payment.order_id)

    async def process_callback(
        self,
        payload: Mapping[str, Any],
        fulfill: FulfillmentHandler | None = None,
    ) -> WebhookResult:
        claim = self.claim_callback(payload)
        if not claim.acquired:
            return self._result_from_unclaimed(claim)
        try:
            if fulfill is not None:
                result = fulfill(claim.payment, claim.callback)
                if inspect.isawaitable(result):
                    await result
        except BaseException as exc:
            claim.fail(exc)
            raise
        payment = claim.complete()
        return WebhookResult(
            payment=payment,
            callback=claim.callback,
            handled=True,
            duplicate=False,
            retry_later=False,
        )
