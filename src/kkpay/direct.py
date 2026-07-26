"""Standalone, self-hosted TRON payment service.

``DirectPaymentService`` creates a local payment intent, displays the
operator's own receiving address, and verifies completed transfers against a
TRON endpoint.  No KKPay gateway, merchant account, callback URL, or package
author IP is involved.
"""

from __future__ import annotations

import inspect
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_UP
from typing import Any

from .errors import KKPayOrderConflictError, KKPayPaymentError, KKPayPaymentNotFoundError
from .models import CallbackData, Order, OrderStatus, TradeType
from .payments import (
    FulfillmentHandler,
    FulfillmentState,
    Payment,
    PaymentClaim,
    SQLitePaymentStore,
    _amount_text,
)
from .tron import (
    DEFAULT_TRON_API_URL,
    AsyncTronClient,
    ChainTransfer,
    TronClient,
    is_valid_tron_address,
    normalize_tron_address,
)


_COIN_STEP = Decimal("0.000001")


@dataclass(frozen=True)
class DirectPaymentResult:
    """Outcome of one direct confirmed-chain payment check."""

    payment: Payment
    transfer: ChainTransfer | None
    callback: CallbackData | None
    handled: bool
    duplicate: bool
    retry_later: bool

    @property
    def paid(self) -> bool:
        """Whether a verified on-chain payment has been recorded locally."""

        return self.payment.status is OrderStatus.PAID


class _DirectPaymentServiceBase:
    """Local order creation and exactly-once state shared by sync/async APIs."""

    def __init__(
        self,
        receiver_address: str,
        store: SQLitePaymentStore,
        *,
        default_timeout: int = 1800,
        amount_increment: Decimal | str | float = _COIN_STEP,
        max_amount_attempts: int = 1000,
    ) -> None:
        address = normalize_tron_address(receiver_address)
        if not is_valid_tron_address(address):
            raise ValueError("receiver_address must be a checksum-valid TRON mainnet address")
        if not isinstance(store, SQLitePaymentStore):
            raise TypeError("store must be a SQLitePaymentStore")
        self.receiver_address = address
        self.store = store
        self.default_timeout = self._timeout(default_timeout)
        self.amount_increment = self._coin_amount(amount_increment, field="amount_increment")
        if self.amount_increment < _COIN_STEP:
            raise ValueError("amount_increment cannot be smaller than 0.000001")
        self.max_amount_attempts = int(max_amount_attempts)
        if not 1 <= self.max_amount_attempts <= 1_000_000:
            raise ValueError("max_amount_attempts must be between 1 and 1000000")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(receiver_address={self.receiver_address!r}, "
            f"store={self.store!r})"
        )

    @staticmethod
    def _timeout(value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("timeout must be between 1 and 86400 seconds")
        try:
            timeout = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("timeout must be between 1 and 86400 seconds") from exc
        if timeout < 1 or timeout > 86400:
            raise ValueError("timeout must be between 1 and 86400 seconds")
        return timeout

    @staticmethod
    def _trade_type(value: object) -> str:
        trade_type = str(value or "").strip()
        if trade_type not in {TradeType.USDT_TRC20, TradeType.TRX}:
            raise ValueError("trade_type must be 'usdt.trc20' or 'tron.trx'")
        return trade_type

    @staticmethod
    def _order_id(value: object) -> str:
        order_id = str(value or "").strip()
        if not order_id:
            raise ValueError("order_id must not be empty")
        return order_id

    @staticmethod
    def _positive_decimal(value: object, *, field: str) -> Decimal:
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a positive number")
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a positive number") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError(f"{field} must be a positive number")
        return amount

    @classmethod
    def _coin_amount(cls, value: object, *, field: str) -> Decimal:
        amount = cls._positive_decimal(value, field=field)
        quantized = amount.quantize(_COIN_STEP)
        if quantized != amount:
            raise ValueError(f"{field} supports at most 6 decimal places")
        return quantized

    def get_payment(self, order_id: str) -> Payment | None:
        return self.store.get_by_order_id(order_id)

    def require_payment(self, order_id: str) -> Payment:
        payment = self.get_payment(order_id)
        if payment is None:
            raise KKPayPaymentNotFoundError("local payment was not found")
        return payment

    def _create_payment(
        self,
        *,
        order_id: str,
        amount: object,
        requested_actual_amount: object,
        trade_type: str,
        timeout: int | None,
        metadata: Mapping[str, Any] | None,
    ) -> Payment:
        requested_order_id = self._order_id(order_id)
        existing = self.store.get_by_order_id(requested_order_id)
        if existing is not None:
            return existing
        display_amount = self._positive_decimal(amount, field="amount")
        base_amount = self._coin_amount(requested_actual_amount, field="actual_amount")
        selected_timeout = self.default_timeout if timeout is None else self._timeout(timeout)
        checked_trade_type = self._trade_type(trade_type)

        for offset in range(self.max_amount_attempts):
            actual_amount = base_amount + self.amount_increment * offset
            # Amounts must remain representable by TRON/USDT's six decimal places.
            actual_amount = self._coin_amount(actual_amount, field="actual_amount")
            trade_id = f"direct_{uuid.uuid4().hex}"
            actual_text = _amount_text(actual_amount)
            order = Order(
                trade_id=trade_id,
                order_id=requested_order_id,
                status=OrderStatus.WAITING,
                amount=_amount_text(display_amount),
                actual_amount=actual_text,
                token=self.receiver_address,
                expiration_time=selected_timeout,
                # A raw checksum-valid T-address is intentional.  The direct QR
                # helper encodes it locally; no remote checkout/IP is required.
                payment_url=self.receiver_address,
                raw={
                    "source": "direct-tron",
                    "trade_id": trade_id,
                    "order_id": requested_order_id,
                    "actual_amount": actual_text,
                    "address": self.receiver_address,
                    "trade_type": checked_trade_type,
                },
            )
            try:
                return self.store.create_direct_payment(
                    order,
                    trade_type=checked_trade_type,
                    metadata=metadata,
                )
            except KKPayOrderConflictError:
                # A concurrent worker may have created the same business order;
                # preserve idempotency before trying a different unique amount.
                existing = self.store.get_by_order_id(requested_order_id)
                if existing is not None:
                    return existing
        raise KKPayPaymentError(
            "could not allocate a unique on-chain amount; use a separate receiving address "
            "or increase max_amount_attempts"
        )

    def create_payment(
        self,
        *,
        order_id: str,
        amount: Decimal | str | int | float,
        trade_type: str = TradeType.USDT_TRC20,
        timeout: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Payment:
        """Create a direct payment where ``amount`` is the coin amount.

        For USDT-TRC20, ``amount`` is USDT; for ``tron.trx``, it is TRX.  The
        service may add the configured tiny unique increment so simultaneous
        orders sent to the same address cannot be confused.
        """

        return self._create_payment(
            order_id=order_id,
            amount=amount,
            requested_actual_amount=amount,
            trade_type=trade_type,
            timeout=timeout,
            metadata=metadata,
        )

    def create_cny_payment(
        self,
        *,
        order_id: str,
        cny_amount: Decimal | str | int | float,
        rate: Decimal | str | int | float,
        trade_type: str = TradeType.USDT_TRC20,
        timeout: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Payment:
        """Create a CNY-priced direct order using the caller's explicit rate.

        ``rate`` is CNY per one USDT/TRX.  It is deliberately caller-supplied:
        this SDK does not depend on a package-author rate service or gateway.
        """

        cny = self._positive_decimal(cny_amount, field="cny_amount")
        cny_rate = self._positive_decimal(rate, field="rate")
        quoted_coin = (cny / cny_rate).quantize(_COIN_STEP, rounding=ROUND_UP)
        return self._create_payment(
            order_id=order_id,
            amount=cny,
            requested_actual_amount=quoted_coin,
            trade_type=trade_type,
            timeout=timeout,
            metadata=metadata,
        )

    @staticmethod
    def _callback_for(payment: Payment, tx_hash: str) -> CallbackData:
        payload = {
            "source": "direct-tron",
            "trade_id": payment.trade_id,
            "order_id": payment.order_id,
            "amount": payment.amount,
            "actual_amount": payment.actual_amount,
            "token": payment.address,
            "block_transaction_id": tx_hash,
            "status": int(OrderStatus.PAID),
        }
        return CallbackData(
            trade_id=payment.trade_id,
            order_id=payment.order_id,
            amount=payment.amount,
            actual_amount=payment.actual_amount,
            token=payment.address,
            block_transaction_id=tx_hash,
            status=OrderStatus.PAID,
            raw=payload,
        )

    @staticmethod
    def _unclaimed_result(
        claim: PaymentClaim,
        transfer: ChainTransfer | None,
    ) -> DirectPaymentResult:
        return DirectPaymentResult(
            payment=claim.payment,
            transfer=transfer,
            callback=claim.callback,
            handled=False,
            duplicate=claim.completed,
            retry_later=not claim.completed,
        )

    @staticmethod
    def _empty_result(payment: Payment) -> DirectPaymentResult:
        return DirectPaymentResult(
            payment=payment,
            transfer=None,
            callback=None,
            handled=False,
            duplicate=False,
            retry_later=False,
        )

    def cancel_payment(self, order_id: str) -> Payment:
        """Cancel a locally-created direct order; no remote gateway is called."""

        payment = self.require_payment(order_id)
        if payment.status is OrderStatus.CANCELLED:
            return payment
        if payment.status in {OrderStatus.PAID, OrderStatus.EXPIRED} or payment.fulfillment_state in {
            FulfillmentState.PROCESSING,
            FulfillmentState.COMPLETED,
        }:
            raise KKPayPaymentError("a paid or expired payment cannot be cancelled")
        return self.store.mark_cancelled(payment.order_id)

    @staticmethod
    def _payment_window(payment: Payment) -> tuple[int, int | None]:
        created_at_ms = int(payment.created_at * 1000)
        expires_at_ms = int(payment.expires_at * 1000) if payment.expires_at is not None else None
        return created_at_ms, expires_at_ms

    @staticmethod
    def _matches_payment_window(transfer: ChainTransfer, payment: Payment) -> bool:
        created_at_ms, expires_at_ms = _DirectPaymentServiceBase._payment_window(payment)
        if transfer.timestamp_ms < created_at_ms:
            return False
        return expires_at_ms is None or transfer.timestamp_ms <= expires_at_ms

    def _finish_sync_claim(
        self,
        claim: PaymentClaim,
        transfer: ChainTransfer | None,
        fulfill: FulfillmentHandler | None,
    ) -> DirectPaymentResult:
        if not claim.acquired:
            return self._unclaimed_result(claim, transfer)
        try:
            if fulfill is not None:
                result = fulfill(claim.payment, claim.callback)
                if inspect.isawaitable(result):
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()
                    raise TypeError("async fulfillment requires AsyncDirectPaymentService")
        except BaseException as exc:
            claim.fail(exc)
            raise
        payment = claim.complete()
        return DirectPaymentResult(
            payment=payment,
            transfer=transfer,
            callback=claim.callback,
            handled=True,
            duplicate=False,
            retry_later=False,
        )

    async def _finish_async_claim(
        self,
        claim: PaymentClaim,
        transfer: ChainTransfer | None,
        fulfill: FulfillmentHandler | None,
    ) -> DirectPaymentResult:
        if not claim.acquired:
            return self._unclaimed_result(claim, transfer)
        try:
            if fulfill is not None:
                result = fulfill(claim.payment, claim.callback)
                if inspect.isawaitable(result):
                    await result
        except BaseException as exc:
            claim.fail(exc)
            raise
        payment = claim.complete()
        return DirectPaymentResult(
            payment=payment,
            transfer=transfer,
            callback=claim.callback,
            handled=True,
            duplicate=False,
            retry_later=False,
        )


class DirectPaymentService(_DirectPaymentServiceBase):
    """Blocking standalone USDT-TRC20/TRX collection service.

    The recipient configures only their own receiving address and a public
    TRON endpoint (or their own full-node endpoint).  No merchant token or
    remote KK gateway URL is required.
    """

    def __init__(
        self,
        receiver_address: str,
        store: SQLitePaymentStore,
        *,
        tron_client: TronClient | None = None,
        api_url: str = DEFAULT_TRON_API_URL,
        api_key: str | None = None,
        default_timeout: int = 1800,
        amount_increment: Decimal | str | float = _COIN_STEP,
        max_amount_attempts: int = 1000,
    ) -> None:
        super().__init__(
            receiver_address,
            store,
            default_timeout=default_timeout,
            amount_increment=amount_increment,
            max_amount_attempts=max_amount_attempts,
        )
        self.tron = tron_client or TronClient(api_url, api_key=api_key)

    def _find_matching_transfer(self, payment: Payment) -> ChainTransfer | None:
        created_at_ms, expires_at_ms = self._payment_window(payment)
        # Query one minute earlier to tolerate index timing, but verification
        # below still requires a block time inside the exact order window.
        transfers = self.tron.list_transfers(
            payment.address,
            trade_type=payment.trade_type,
            min_timestamp_ms=max(0, created_at_ms - 60_000),
        )
        expected_amount = Decimal(payment.actual_amount)
        for transfer in transfers:
            if transfer.amount != expected_amount or not self._matches_payment_window(transfer, payment):
                continue
            if self.tron.verify_transfer(
                transfer,
                address=payment.address,
                amount=expected_amount,
                created_at_ms=created_at_ms,
                expires_at_ms=expires_at_ms,
            ):
                return transfer
        return None

    def poll_payment(
        self,
        order_id: str,
        fulfill: FulfillmentHandler | None = None,
    ) -> DirectPaymentResult:
        """Verify chain data, then run fulfillment exactly once if paid.

        This is the direct-mode substitute for a gateway callback.  It is safe
        to call repeatedly from a scheduled worker or a user "check payment"
        button: a verified transaction is bound to the local order and the
        SQLite lease prevents double fulfillment.
        """

        payment = self.require_payment(order_id)
        if payment.status in {OrderStatus.CANCELLED, OrderStatus.EXPIRED}:
            return self._empty_result(payment)
        if payment.status is OrderStatus.PAID:
            if payment.fulfillment_state is FulfillmentState.COMPLETED:
                return DirectPaymentResult(
                    payment=payment,
                    transfer=None,
                    callback=self._callback_for(payment, payment.block_transaction_id),
                    handled=False,
                    duplicate=True,
                    retry_later=False,
                )
            if not payment.block_transaction_id:
                raise KKPayPaymentError("paid direct payment is missing a transaction hash")
            callback = self._callback_for(payment, payment.block_transaction_id)
            return self._finish_sync_claim(self.store.claim_callback(callback), None, fulfill)

        transfer = self._find_matching_transfer(payment)
        if transfer is None:
            # Do not expire before scanning: a transaction may have reached a
            # confirmed endpoint slightly after the local deadline while its
            # authoritative block timestamp is still inside the order window.
            if payment.expires_at is not None and time.time() >= payment.expires_at:
                payment = self.store.expire_if_due(payment.order_id)
            return self._empty_result(payment)
        callback = self._callback_for(payment, transfer.tx_hash)
        return self._finish_sync_claim(self.store.claim_callback(callback), transfer, fulfill)

    def poll_pending(
        self,
        fulfill: FulfillmentHandler | None = None,
        *,
        limit: int = 100,
    ) -> list[DirectPaymentResult]:
        """Poll up to ``limit`` local waiting/unfinished paid payments."""

        return [self.poll_payment(payment.order_id, fulfill) for payment in self.store.list_open_payments(limit=limit)]


class AsyncDirectPaymentService(_DirectPaymentServiceBase):
    """Asyncio-native standalone collection service for Telethon/ASGI apps."""

    def __init__(
        self,
        receiver_address: str,
        store: SQLitePaymentStore,
        *,
        tron_client: AsyncTronClient | None = None,
        api_url: str = DEFAULT_TRON_API_URL,
        api_key: str | None = None,
        default_timeout: int = 1800,
        amount_increment: Decimal | str | float = _COIN_STEP,
        max_amount_attempts: int = 1000,
    ) -> None:
        super().__init__(
            receiver_address,
            store,
            default_timeout=default_timeout,
            amount_increment=amount_increment,
            max_amount_attempts=max_amount_attempts,
        )
        self.tron = tron_client or AsyncTronClient(api_url, api_key=api_key)

    async def create_payment(
        self,
        *,
        order_id: str,
        amount: Decimal | str | int | float,
        trade_type: str = TradeType.USDT_TRC20,
        timeout: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Payment:
        return self._create_payment(
            order_id=order_id,
            amount=amount,
            requested_actual_amount=amount,
            trade_type=trade_type,
            timeout=timeout,
            metadata=metadata,
        )

    async def create_cny_payment(
        self,
        *,
        order_id: str,
        cny_amount: Decimal | str | int | float,
        rate: Decimal | str | int | float,
        trade_type: str = TradeType.USDT_TRC20,
        timeout: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Payment:
        cny = self._positive_decimal(cny_amount, field="cny_amount")
        cny_rate = self._positive_decimal(rate, field="rate")
        quoted_coin = (cny / cny_rate).quantize(_COIN_STEP, rounding=ROUND_UP)
        return self._create_payment(
            order_id=order_id,
            amount=cny,
            requested_actual_amount=quoted_coin,
            trade_type=trade_type,
            timeout=timeout,
            metadata=metadata,
        )

    async def _find_matching_transfer(self, payment: Payment) -> ChainTransfer | None:
        created_at_ms, expires_at_ms = self._payment_window(payment)
        transfers = await self.tron.list_transfers(
            payment.address,
            trade_type=payment.trade_type,
            min_timestamp_ms=max(0, created_at_ms - 60_000),
        )
        expected_amount = Decimal(payment.actual_amount)
        for transfer in transfers:
            if transfer.amount != expected_amount or not self._matches_payment_window(transfer, payment):
                continue
            if await self.tron.verify_transfer(
                transfer,
                address=payment.address,
                amount=expected_amount,
                created_at_ms=created_at_ms,
                expires_at_ms=expires_at_ms,
            ):
                return transfer
        return None

    async def poll_payment(
        self,
        order_id: str,
        fulfill: FulfillmentHandler | None = None,
    ) -> DirectPaymentResult:
        payment = self.require_payment(order_id)
        if payment.status in {OrderStatus.CANCELLED, OrderStatus.EXPIRED}:
            return self._empty_result(payment)
        if payment.status is OrderStatus.PAID:
            if payment.fulfillment_state is FulfillmentState.COMPLETED:
                return DirectPaymentResult(
                    payment=payment,
                    transfer=None,
                    callback=self._callback_for(payment, payment.block_transaction_id),
                    handled=False,
                    duplicate=True,
                    retry_later=False,
                )
            if not payment.block_transaction_id:
                raise KKPayPaymentError("paid direct payment is missing a transaction hash")
            callback = self._callback_for(payment, payment.block_transaction_id)
            return await self._finish_async_claim(self.store.claim_callback(callback), None, fulfill)

        transfer = await self._find_matching_transfer(payment)
        if transfer is None:
            if payment.expires_at is not None and time.time() >= payment.expires_at:
                payment = self.store.expire_if_due(payment.order_id)
            return self._empty_result(payment)
        callback = self._callback_for(payment, transfer.tx_hash)
        return await self._finish_async_claim(self.store.claim_callback(callback), transfer, fulfill)

    async def poll_pending(
        self,
        fulfill: FulfillmentHandler | None = None,
        *,
        limit: int = 100,
    ) -> list[DirectPaymentResult]:
        results: list[DirectPaymentResult] = []
        for payment in self.store.list_open_payments(limit=limit):
            results.append(await self.poll_payment(payment.order_id, fulfill))
        return results
