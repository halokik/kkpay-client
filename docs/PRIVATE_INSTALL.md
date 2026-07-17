# 私有安装与发布

## 推荐：私有 Git 固定标签

这是目前最简单、最少运维的方案，不需要额外部署包索引服务。

```bash
pip install "kkpay-client @ git+ssh://git@github.com/halokik/kkpay-client.git@v0.2.0"
```

要求部署机器已经配置只读 GitHub Deploy Key，或使用有权读取私有仓库的 SSH Key。不要把 GitHub Token 写进 `requirements.txt`。

升级时先在 SDK 仓库运行测试并创建新标签，然后在机器人项目中修改标签版本。生产项目不要直接跟随 `main`。

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
pip install --index-url "https://pypi.example.com/simple/" kkpay-client==0.2.0
```

凭据应通过 CI Secret、环境变量或机器级 pip 配置注入，不要写入源码、命令历史或项目依赖文件。

## 新机器人接入清单

1. 在 KK 网关中为机器人创建独立商户和收款地址。
2. 把 `KKPAY_BASE_URL`、`KKPAY_MERCHANT_ID`、`KKPAY_API_TOKEN` 放入机器人私有配置。
3. 本地订单至少保存 `order_id`、`trade_id`、用户、人民币金额、实付币数量、地址、状态和发货信息。
4. 暴露 HTTPS 回调地址，验签并核对本地订单字段。
5. 使用业务库唯一约束/事务和 SDK 幂等库，确保重复回调不重复发货。
6. 冒烟验证创建、查单、取消和重复回调，再精确重启目标 PM2 服务。
