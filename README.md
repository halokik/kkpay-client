# kkpay-client

Typed synchronous and asynchronous Python client for a KKPay-compatible USDT-TRC20/TRX payment gateway.

## Install

```bash
pip install kkpay-client
```

## Async example

```python
from kkpay import AsyncKKPayClient

client = AsyncKKPayClient(
    base_url="https://pay.example.com",
    merchant_id="my_bot",
    api_token="read-from-environment",
)

order = await client.create_order(
    order_id="VIP_123_1720000000",
    amount=100,
    notify_url="https://bot.example.com/kkpay/notify",
    redirect_url="https://t.me/my_bot",
    trade_type="usdt.trc20",
    timeout=1800,
)
print(order.payment_url, order.actual_amount)
```

The blocking `KKPayClient` exposes the same methods without `await`:

```python
status = client.query_order(order.trade_id)
client.cancel_order(order.trade_id)
```

## Secure callback handling

Always load the local order and enforce idempotency in your application. The SDK verifies the signature and can bind the callback to the expected identifiers:

```python
from fastapi import FastAPI, Request, Response
from kkpay import OrderStatus

app = FastAPI()

@app.post("/kkpay/notify")
async def kkpay_notify(request: Request):
    payload = await request.json()
    local_order = load_order(payload.get("order_id"))
    callback = client.verify_callback(
        payload,
        expected_order_id=local_order.order_id,
        expected_trade_id=local_order.trade_id,
    )
    assert callback.status is OrderStatus.PAID
    fulfill_once(local_order, callback.block_transaction_id)
    return Response("ok", media_type="text/plain")
```

Do not credit an order based only on a successful query response. Verify callbacks, compare the stored amount and identifiers, and make fulfillment idempotent.

## Security

- Secrets are never included in `repr(client)`.
- Public plain HTTP is rejected by default. Use HTTPS. Localhost HTTP is allowed for same-host integrations.
- `allow_insecure_http=True` exists only for migration from a legacy private deployment.
- The gateway's legacy lowercase MD5 signature is supported for protocol compatibility; it does not replace HTTPS.

## Supported operations

- Create an order: `create_order(...)`
- Query an order: `query_order(trade_id)`
- Cancel an order: `cancel_order(trade_id)`
- Verify and parse a callback: `verify_callback(payload, ...)`
- Standalone signing helpers: `make_signature(...)`, `verify_signature(...)`

## License

MIT

