# kkpay-client

KK 收款网关的 Python SDK，支持同步/异步下单、查单、取消、回调验签、金额绑定校验、网络重试和 SQLite 回调幂等。

> 推荐架构：每个机器人安装 SDK、使用独立商户凭据、保存自己的订单并执行自己的发货逻辑。SDK 不保存商户 Token，也不代替业务订单表。

## 安装

仓库已公开，推荐固定版本标签安装，避免生产项目意外跟随 `main`：

```bash
pip install "kkpay-client @ git+https://github.com/halokik/kkpay-client.git@v0.2.0"
```

也可以写入项目的 `requirements.txt`：

```text
kkpay-client @ git+https://github.com/halokik/kkpay-client.git@v0.2.0
```

本机开发：

```bash
pip install -e /opt/kkpay-client
```

安装、发布及私有部署方式见 [`docs/INSTALL.md`](docs/INSTALL.md)。

## 异步下单

同机机器人优先访问回环地址，商户信息从环境变量或不入库的配置文件读取：

```python
import os

from kkpay import AsyncKKPayClient, TradeType

client = AsyncKKPayClient(
    base_url="http://127.0.0.1:6688",
    merchant_id=os.environ["KKPAY_MERCHANT_ID"],
    api_token=os.environ["KKPAY_API_TOKEN"],
)

order = await client.create_order(
    order_id="VIP_123_1720000000",       # 重试时必须复用同一个订单号
    amount=100,                           # 人民币金额
    notify_url="https://bot.example.com/kkpay/notify",
    redirect_url="https://t.me/example_bot",
    trade_type=TradeType.USDT_TRC20,      # 或 TradeType.TRX
    timeout=1800,
)

print(order.trade_id, order.actual_amount, order.address, order.payment_url)
```

同步代码使用 `KKPayClient`，方法相同但不需要 `await`：

```python
from kkpay import KKPayClient

sync_client = KKPayClient(
    base_url="http://127.0.0.1:6688",
    merchant_id=os.environ["KKPAY_MERCHANT_ID"],
    api_token=os.environ["KKPAY_API_TOKEN"],
)
status = sync_client.query_order(order.trade_id)
sync_client.cancel_order(order.trade_id)
```

订单状态：`WAITING=1`、`PAID=2`、`EXPIRED=3`、`CANCELLED=4`。

## 安全回调与幂等发货

回调必须先验签，再和本地订单的订单号、网关单号、人民币金额、币数量及收款地址逐项比对。只有本地订单从“待支付”原子地切换为“已支付”后才执行发货。

```python
from fastapi import HTTPException, Request
from fastapi.responses import PlainTextResponse
from kkpay import SQLiteIdempotencyStore

dedupe = SQLiteIdempotencyStore("data/kkpay_webhooks.db")

@app.post("/kkpay/notify")
async def kkpay_notify(request: Request):
    payload = await request.json()

    # 先验签后再相信 payload 中的订单号。
    callback = client.verify_callback(payload)
    local_order = await load_local_order(callback.order_id)
    if local_order is None:
        raise HTTPException(404, "unknown order")

    callback = client.verify_callback(
        payload,
        expected_order_id=local_order.order_id,
        expected_trade_id=local_order.trade_id,
        expected_amount=local_order.amount,
        expected_actual_amount=local_order.actual_amount,
        expected_address=local_order.address,
    )

    with dedupe.claim(callback) as claim:
        if not claim.acquired:
            if claim.completed:
                return PlainTextResponse("ok")
            # 另一 worker 正在处理，不要提前确认成功，让网关稍后重试。
            raise HTTPException(409, "callback is processing")

        # 此函数内部仍应使用业务库事务/唯一约束保证只发货一次。
        await mark_paid_and_fulfill_once(local_order, callback)

    return PlainTextResponse("ok")
```

如果发货抛出异常，幂等记录会进入 `failed`，下一次网关回调可以重新领取；处理完成的回调会直接返回 `ok`，不会重复发货。

## 自动重试

默认对网络异常及 `408/425/429/5xx` 最多尝试 3 次。创建订单的重试安全性依赖**同一商户始终复用同一个 `order_id`**。

```python
from kkpay import RetryPolicy

client = AsyncKKPayClient(
    base_url="http://127.0.0.1:6688",
    merchant_id="example_bot",
    api_token="read-from-environment",
    retry_policy=RetryPolicy(attempts=4, backoff_seconds=0.3),
)
```

## 安全约束

- `repr(client)` 不会输出 `api_token`。
- 公网明文 HTTP 默认拒绝；同机 `127.0.0.1` HTTP 可用。
- 不要把商户 Token、机器人 Token、数据库和 `.env` 放进 SDK 仓库。
- 每个机器人使用独立 `merchant_id/api_token`，不要共用管理员全局 Token。
- 查单只用于展示或补偿，不应绕过回调验签直接发货。
- 网关回调字段 `token` 是**收款地址**，不是 API Token；SDK 同时提供更清晰的 `.address` 属性。

## 开发

```bash
python -m pip install -e '.[test]'
python -m pytest
python -m build
```

完整 FastAPI 示例见 [`examples/fastapi_webhook.py`](examples/fastapi_webhook.py)。

## License

MIT
