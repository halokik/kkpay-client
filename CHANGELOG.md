# Changelog

## 0.2.0 - 2026-07-17

- Add persistent SQLite callback idempotency with failure retry and stale-lock recovery.
- Add callback binding checks for order ID, trade ID, amounts, and receiving address.
- Add configurable retries for transient network and gateway failures.
- Normalize and validate order amounts, timeout, and required redirect URL.
- Add private Git/private PyPI installation guidance and a FastAPI example.
- Replace the public PyPI publishing workflow with private build artifacts.

## 0.1.0 - 2026-07-11

- Initial typed synchronous and asynchronous KKPay client.
