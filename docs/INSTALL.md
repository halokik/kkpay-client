# 安装、发布与私有部署

## 推荐：公开 Git 固定标签

这是目前最简单、最少运维的方案，不需要额外部署包索引服务或配置 GitHub 凭据。

```bash
pip install "kkpay-client @ git+https://github.com/halokik/kkpay-client.git@v0.3.0"
```

生产项目应固定版本标签，不要直接跟随 `main`。升级时先在 SDK 仓库运行测试并创建新标签，再在机器人项目中修改标签版本。

需要 FastAPI Webhook 路由时安装可选依赖：

```bash
pip install "kkpay-client[fastapi] @ git+https://github.com/halokik/kkpay-client.git@v0.3.0"
```

## 可选：内部私有分发

若需在内网或私有 fork 中分发，可通过只读 Deploy Key 或私有 PyPI 安装。不要把 GitHub Token 写进 `requirements.txt`。

## 可选：上传到已有私有 PyPI

仅在已经有 devpi、pypiserver、Artifactory 等内部索引时使用：

```bash
python -m build
TWINE_REPOSITORY_URL="https://pypi.example.com/" \
TWINE_USERNAME="__token__" \
TWINE_PASSWORD="$PRIVATE_PYPI_TOKEN" \
python -m twine upload --repository-url "$TWINE_REPOSITORY_URL" dist/*
```

安装：

```bash
pip install --index-url "https://pypi.example.com/simple/" kkpay-client==0.3.0
```

凭据应通过 CI Secret、环境变量或机器级 pip 配置注入，不要写入源码、命令历史或项目依赖文件。

## 新机器人接入清单

1. 在 KK 网关中为机器人创建独立商户和收款地址。
2. 把 `KKPAY_BASE_URL`、`KKPAY_MERCHANT_ID`、`KKPAY_API_TOKEN` 放入机器人私有配置；本机回环调用时另设公开收银台域名 `KKPAY_CHECKOUT_BASE_URL`。
3. 使用 `SQLitePaymentStore` 保存本地订单、收款地址、实付币数量、状态、发货租约和必要业务 metadata。
4. 使用 `payment_qr_png(payment)` 生成当前订单的收银台二维码，并同时展示实际币金额和地址。
5. 暴露 HTTPS 回调地址，通过 `PaymentService.process_callback()` 或 `create_fastapi_router()` 验签、核对字段并一次性发货。
6. 冒烟验证创建、查单、取消和重复回调，再精确重启目标 PM2 服务。
