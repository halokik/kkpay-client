# kkpay-client

面向商户应用的完整 USDT/TRX 收款 SDK，适配 KKPay 兼容网关。

它不仅封装接口调用，还提供：**创建并持久化订单、收银台二维码、订单查询与取消、回调验签、字段绑定、失败重试和一次性发货领取**。机器人或网站只需要实现自己的业务发货函数。

> 架构边界：SDK 负责商户侧订单流程；现有 KK 网关负责链上监听、地址池、私钥、归集和商户管理。不要把私钥或链上监听逻辑复制进公开 SDK。

## 安装

固定版本标签安装，避免生产项目意外跟随 `main`：

```bash
pip install "kkpay-client @ git+https://github.com/halokik/kkpay-client.git@v0.3.0"
```

FastAPI 回调路由额外安装：

```bash
pip install "kkpay-client[fastapi] @ git+https://github.com/halokik/kkpay-client.git@v0.3.0"
```

安装、发布及私有部署方式见 [`docs/INSTALL.md`](docs/INSTALL.md)。

## 一次调用创建完整收款订单

同机机器人应使用回环网关地址；凭据只从环境变量或未跟踪配置读取。

```python
import os
from pathlib import Path

from kkpay import KKPayClient, PaymentService, SQLitePaymentStore, payment_qr_png

client = KKPayClient(
    base_url=os.getenv("KKPAY_BASE_URL", "http://127.0.0.1:6688"),
    merchant_id=os.environ["KKPAY_MERCHANT_ID"],
    api_token=os.environ["KKPAY_API_TOKEN"],
)
payments = PaymentService(
    client,
    SQLitePaymentStore("data/kkpay_payments.sqlite"),
    # 网关通过 127.0.0.1 调用时，填写给用户访问的 HTTPS 收银台域名。
    checkout_base_url=os.getenv("KKPAY_CHECKOUT_BASE_URL"),
)

payment = payments.create_payment(
    order_id="VIP_123_1720000000",  # 同一业务订单始终复用同一 order_id
    amount=100,                      # 网关计价金额：人民币
    notify_url="https://bot.example.com/kkpay/notify",
    redirect_url="https://t.me/example_bot",
    metadata={"user_id": 123, "product": "vip-month"},
)

# 发送给 Telegram 用户，或交给 Web 前端显示。
Path("/tmp/payment.png").write_bytes(payment_qr_png(payment))
print(payment.payment_url, payment.address, payment.actual_amount)
```

二维码编码的是短期有效的 `payment_url`，而不是裸收款地址；用户打开后可核对网络、金额和订单。仍应同时展示 `actual_amount`、`address`、过期时间和“打开收银台”按钮。

若 SDK 通过 `http://127.0.0.1:6688` 调用网关，网关可能把回环 Host 写进返回的 `payment_url`。此时设置 `KKPAY_CHECKOUT_BASE_URL=https://pay.example.com`，SDK 会只替换收银台链接的主机名（保留订单路径），让二维码可被外部用户打开。

重复调用同一个已保存的 `order_id` 会直接返回本地订单，不会再次创建网关订单。

## 安全回调与一次性发货

SDK 会自动完成签名验签，并绑定本地 `order_id`、`trade_id`、法币金额、实际币金额和收款地址。`process_callback` 用 SQLite 租约保证同一回调只进入一次发货函数；发货异常会释放租约，供网关重试。

```python
def fulfill(payment, callback):
    # 在你的业务数据库事务/唯一约束中：
    # 1. 标记订单已支付；2. 发会员/余额/商品；3. 记录 tx hash。
    grant_product_once(payment.metadata["user_id"], payment.order_id)


def receive_kkpay_callback(payload: dict) -> tuple[str, int]:
    result = payments.process_callback(payload, fulfill)
    if result.retry_later:
        # 正在由另一 worker 发货，让网关稍后重试。
        return "processing", 409
    # 新回调和已完成的重复回调都要返回网关要求的 ok。
    return "ok", 200
```

可直接接入 FastAPI：

```python
from fastapi import FastAPI
from kkpay import create_fastapi_router

app = FastAPI()
app.include_router(create_fastapi_router(payments, fulfill))
```

完整示例见 [`examples/fastapi_webhook.py`](examples/fastapi_webhook.py)。异步机器人可改用 `AsyncKKPayClient` 与 `AsyncPaymentService`；其 `create_payment`、`query_payment`、`cancel_payment` 和 `process_callback` 均可 `await`。

## 查询、取消与对账

```python
status = payments.query_payment(payment.order_id)  # 仅用于展示、对账或补偿
cancelled = payments.cancel_payment(payment.order_id)
```

查单不能绕过已签名回调直接触发发货。链上支付成功的最终业务动作始终应经过 `process_callback` 和你的业务库事务。

## 低层接口仍可使用

`KKPayClient` / `AsyncKKPayClient` 仍提供同步和异步下单、查单、取消与验签，适合已有项目渐进迁移。`SQLiteIdempotencyStore` 也保留用于只需要回调去重的旧项目。

订单状态：`WAITING=1`、`PAID=2`、`EXPIRED=3`、`CANCELLED=4`；支持 `TradeType.USDT_TRC20` 与 `TradeType.TRX`。

## 安全约束

- SDK 不保存 `api_token`、私钥或助记词；不要把它们放进源码、日志、二维码或 `metadata`。
- 每个机器人使用独立 `merchant_id/api_token`，不要共享管理员全局 Token。
- SQLite 账本是支付流程的一部分：使用持久化磁盘、定期备份，并加入项目 `.gitignore`。
- 公网明文 HTTP 默认拒绝；同机 `127.0.0.1` HTTP 可用，公网网关应使用 HTTPS。
- 回调未知订单、签名不符、金额/地址不符、已过期或已取消时必须拒绝，不能发货。
- QR 图仅对应当前订单；订单过期后请使用新的 `order_id` 创建新的支付尝试和二维码。

## 开发

```bash
python -m pip install -e '.[test,fastapi]'
python -m pytest
python -m build
```

## License

MIT
