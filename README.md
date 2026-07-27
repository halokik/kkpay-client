# kkpay-client

面向商户应用的完整 USDT-TRC20/TRX 收款 SDK。**默认推荐独立自托管模式**：
收款应用直接查询并二次验证 TRON 链上确认交易，不调用 KK 收款网关，也不依赖本仓库作者的 IP。

独立模式提供：**本地订单账本、唯一实付金额、地址二维码、TRON 链上二次验真、超时/取消、轮询，以及一次性发货租约**。机器人或网站只需要实现自己的业务发货函数。

> 独立模式不接触私钥：收款地址、TronGrid API Key 或自建全节点地址都由每个使用者自己配置。SDK 只读取公开链上数据，不能转走用户资产。

## 安装

当前独立模式将在下一个 `v0.4.0` 发布标签中提供。发布后固定标签安装，避免生产项目意外跟随 `main`：

```bash
pip install "kkpay-client @ git+https://github.com/halokik/kkpay-client.git@v0.4.0"
```

## Telegram 支付 UI Skill

仓库附带一个可复用的 Codex Skill：
[`skills/kkpay-telegram-payment`](skills/kkpay-telegram-payment)。它提供
@mtp22bot 风格的会员套餐选择页、Telegram 高级表情按钮以及对接本 SDK 的
Telethon 模板；其中不包含任何商户密钥、收款地址或特定支付商的签名协议。

可使用 Codex 的 skill installer 从 GitHub 安装：

```bash
python3 /path/to/install-skill-from-github.py \
  --repo halokik/kkpay-client \
  --path skills/kkpay-telegram-payment
```

直接收款接入请使用下文的 `DirectPaymentService`；如需第三方托管支付，保留
模板 UI 并调用目标机器人已有的、经过配置的支付适配器。

## 独立收款：不使用 KK 网关

最小配置只需**使用者自己的收款地址**。可选的 `TRON_API_URL` 可以是 TronGrid，或使用者自己的 TRON 全节点/代理；它不是本项目作者的服务器。

```python
import os
from pathlib import Path

from kkpay import DirectPaymentService, SQLitePaymentStore, payment_qr_png

payments = DirectPaymentService(
    receiver_address=os.environ["TRON_RECEIVER_ADDRESS"],  # 使用者自己的 T 地址
    store=SQLitePaymentStore("data/direct_payments.sqlite"),
    api_url=os.getenv("TRON_API_URL", "https://api.trongrid.io"),
    api_key=os.getenv("TRON_PRO_API_KEY") or None,         # 使用者自己的 key，可选
)

# amount 是链上实付数量。USDT-TRC20 使用 USDT；tron.trx 使用 TRX。
payment = payments.create_payment(
    order_id="VIP_123_1720000000",
    amount="13.89",
    metadata={"user_id": 123, "product": "vip-month"},
)

# 二维码只编码自己的收款 T 地址；消息中必须同时展示 exact actual_amount。
Path("/tmp/payment.png").write_bytes(payment_qr_png(payment))
print(payment.address, payment.actual_amount, payment.expires_at)
```

若业务价格以人民币计价，请由业务方明确传入自己的汇率，不使用共享汇率服务：

```python
payment = payments.create_cny_payment(
    order_id="VIP_124_1720000000",
    cny_amount=100,
    rate="7.20",  # 1 USDT = 7.20 CNY，由你的业务自行维护
)
```

同一收款地址上的未完成订单会自动分配 `0.000001` 级别的唯一实付金额，例如 `13.89`、`13.890001`，避免一笔转账匹配多个订单。每个部署仍建议使用自己的地址和独立 SQLite 数据库。

### 本地轮询与一次性发货

直接模式没有第三方回调。由机器人自己的定时任务或“检查支付”按钮调用 `poll_payment()`；SDK 先从确认节点读取交易，再用交易详情二次校验地址、币种、金额和订单时间窗口，最后通过 SQLite 租约只发货一次。

```python
def fulfill(payment, callback):
    grant_product_once(payment.metadata["user_id"], payment.order_id)


result = payments.poll_payment(payment.order_id, fulfill)
if result.handled:
    print("paid:", result.callback.block_transaction_id)
elif result.duplicate:
    print("already fulfilled")

# 后台任务可周期性执行：
# payments.poll_pending(fulfill, limit=100)
```

`AsyncDirectPaymentService` 提供相同的 `await create_payment()`、`await poll_payment()` 和 `await poll_pending()` 接口，适合 Telethon、FastAPI 等 asyncio 项目。

## 兼容旧 KK 网关模式

旧的 `KKPayClient` / `PaymentService` 仍保留，以免破坏既有项目；它们需要一个 KKPay 兼容网关。新项目如果目标是不依赖你的 IP，应使用上面的 `DirectPaymentService`，不要配置 `KKPAY_BASE_URL`。

FastAPI 网关回调路由额外安装：

```bash
pip install "kkpay-client[fastapi] @ git+https://github.com/halokik/kkpay-client.git@v0.4.0"
```

安装、发布及私有部署方式见 [`docs/INSTALL.md`](docs/INSTALL.md)。

### 一次调用创建网关收款订单（旧兼容模式）

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

### 安全回调与一次性发货（旧网关模式）

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

### 查询、取消与对账（旧网关模式）

```python
status = payments.query_payment(payment.order_id)  # 仅用于展示、对账或补偿
cancelled = payments.cancel_payment(payment.order_id)
```

查单不能绕过已签名回调直接触发发货。链上支付成功的最终业务动作始终应经过 `process_callback` 和你的业务库事务。

### 低层接口仍可使用（旧网关模式）

`KKPayClient` / `AsyncKKPayClient` 仍提供同步和异步下单、查单、取消与验签，适合已有项目渐进迁移。`SQLiteIdempotencyStore` 也保留用于只需要回调去重的旧项目。

订单状态：`WAITING=1`、`PAID=2`、`EXPIRED=3`、`CANCELLED=4`；支持 `TradeType.USDT_TRC20` 与 `TradeType.TRX`。

## 安全约束

- 独立模式不保存私钥或助记词；不要把它们放进源码、日志、二维码、SQLite 或 `metadata`。收款地址对应的钱包由使用者自己保管。
- `TRON_PRO_API_KEY` 不是私钥，但仍应通过环境变量或未跟踪配置传入，不要提交到仓库。
- 直接模式必须持续运行自己的 `poll_payment()` / `poll_pending()` 任务；SDK 不会向你的应用服务器发送外部回调。
- 每个部署使用独立 SQLite 账本和接收地址；若必须共享地址，则共享同一个持久化账本，不能让多个独立数据库各自分配唯一金额。
- 只有链上二次验证通过且订单仍在精确时间窗口内的交易才能发货；未知、取消、过期或金额/地址不符订单必须拒绝。
- 旧网关模式下，每个机器人使用独立 `merchant_id/api_token`，不要共享管理员全局 Token。
- SQLite 账本是支付流程的一部分：使用持久化磁盘、定期备份，并加入项目 `.gitignore`。
- 公网明文 HTTP 默认拒绝；若使用自建 TRON 节点或旧网关，公网端点应使用 HTTPS。
- 直接模式 QR 仅是收款地址，必须同时展示当前订单的 `actual_amount` 和过期时间；订单过期后请使用新的 `order_id` 创建新的支付尝试。

## 开发

```bash
python -m pip install -e '.[test,fastapi]'
python -m pytest
python -m build
```

## License

MIT
