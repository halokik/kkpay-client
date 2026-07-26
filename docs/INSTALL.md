# 安装、发布与自托管部署

## 推荐：独立 TRON 收款模式

`v0.4.0` 发布后，使用固定标签安装：

```bash
pip install "kkpay-client @ git+https://github.com/halokik/kkpay-client.git@v0.4.0"
```

它不需要 `KKPAY_BASE_URL`、`merchant_id` 或 `api_token`。每个使用者自行准备：

1. 自己控制的 TRON 收款地址（`TRON_RECEIVER_ADDRESS`）；
2. 一个能读取确认交易的 TRON 端点：默认 `https://api.trongrid.io`，也可配置自己的全节点/代理为 `TRON_API_URL`；
3. 可选的、属于使用者自己的 `TRON_PRO_API_KEY`。

示例环境变量：

```bash
TRON_RECEIVER_ADDRESS=Txxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TRON_API_URL=https://api.trongrid.io
TRON_PRO_API_KEY=your-own-key
```

收款程序使用 `DirectPaymentService` 创建本地订单，并从自己的机器人后台任务周期性调用 `poll_pending()`。该进程直接读取链上确认交易；无需访问包作者服务器或 `/opt/beusdt`。

使用者仍必须保护自己的钱包私钥，但私钥**不应**配置给 SDK：SDK 只需要收款地址和公共链查询权限。

## 可选：FastAPI 旧网关回调

仅在兼容已有 KKPay 网关项目时安装：

```bash
pip install "kkpay-client[fastapi] @ git+https://github.com/halokik/kkpay-client.git@v0.4.0"
```

新项目不需要 FastAPI 回调来确认直接收款；直接模式用本地链上轮询。旧 `KKPayClient` / `PaymentService` API 仍可用，但它们仍要求一个 KKPay 兼容网关。

## 发布新版本

生产项目应固定版本标签，不要直接跟随 `main`。发布前在 SDK 仓库中运行：

```bash
python -m pip install -e '.[test,fastapi]'
python -m pytest
python -m build
```

验证通过后创建与 `pyproject.toml` 一致的 Git 标签并推送。不要把钱包私钥、TronGrid key、GitHub Token 或 SQLite 订单库打包进 release。

## 可选：内部私有分发

若需在内网或私有 fork 中分发，可通过只读 Deploy Key 或私有 PyPI 安装。不要把 GitHub Token 写进 `requirements.txt`。只有已具备 devpi、pypiserver、Artifactory 等内部索引时，才上传 wheel：

```bash
python -m build
TWINE_REPOSITORY_URL="https://pypi.example.com/" \
TWINE_USERNAME="__token__" \
TWINE_PASSWORD="$PRIVATE_PYPI_TOKEN" \
python -m twine upload --repository-url "$TWINE_REPOSITORY_URL" dist/*
```
