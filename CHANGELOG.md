# Changelog

## Unreleased

- Add the public `kkpay-telegram-payment` Skill with an @mtp22bot-style
  membership plan picker, premium Telegram emoji button IDs, and a
  provider-neutral Telethon payment-menu template.

## 0.4.0 - 2026-07-26

- Add `DirectPaymentService` and `AsyncDirectPaymentService`: standalone TRON collection that does not call a KKPay gateway or a package-author IP.
- Add direct confirmed-chain transfer listing and transaction-detail verification for USDT-TRC20 and native TRX.
- Add atomic unique exact-amount reservation, local expiry/cancellation, direct QR address encoding, and SQLite-backed exactly-once fulfillment after verified chain settlement.
- Keep legacy `KKPayClient` / `PaymentService` APIs as an explicit compatibility mode for existing gateway deployments.

## 0.3.0 - 2026-07-17

- Add complete merchant-side payment orchestration with a durable SQLite order ledger.
- Add payment checkout QR PNG generation from the expiring gateway payment URL.
- Add callback-to-order binding, fulfillment leases, duplicate suppression, failure retry, and safe cancellation helpers.
- Add synchronous and asyncio-native payment services plus an optional FastAPI webhook router.
- Keep chain monitoring, addresses, private keys, and merchant administration in the gateway rather than the public SDK.

## 0.2.0 - 2026-07-17

- Add persistent SQLite callback idempotency with failure retry and stale-lock recovery.
- Add callback binding checks for order ID, trade ID, amounts, and receiving address.
- Add configurable retries for transient network and gateway failures.
- Normalize and validate order amounts, timeout, and required redirect URL.
- Add private Git/private PyPI installation guidance and a FastAPI example.
- Replace the public PyPI publishing workflow with private build artifacts.

## 0.1.0 - 2026-07-11

- Initial typed synchronous and asynchronous KKPay client.
