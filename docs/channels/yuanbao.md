# 元宝接入指南

## 概述

通过腾讯元宝（原腾讯混元助手）的开放接口将 Echo Agent 接入元宝平台，实现与用户在元宝生态中的智能对话。

## 前置条件

- 一个腾讯云账号
- 在腾讯云开通混元大模型服务
- 获取腾讯云 API 密钥（SecretId 和 SecretKey）
- 了解腾讯元宝的插件/机器人接入方式

## 5 分钟快速接入

### 1. 获取凭证

1. 访问 [腾讯云控制台](https://console.cloud.tencent.com/)，登录账号
2. 在「云产品」中搜索并开通「混元大模型」服务
3. 在「访问管理」→「API 密钥管理」中创建密钥：
   - 记录 SecretId（如 `AKIDxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`）
   - 记录 SecretKey
4. 在元宝开放平台（如有）注册开发者并创建机器人应用

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择元宝，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| Secret ID | 腾讯云 API SecretId | `AKIDxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| Secret Key | 腾讯云 API SecretKey | `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| 应用 ID | 元宝机器人应用 ID | `xxxxxxxx` |
| 回调 URL | 接收消息的地址 | `https://your-domain.com/api/channels/yuanbao/webhook` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  yuanbao:
    secret_id: "AKIDxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    secret_key: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    app_id: "xxxxxxxx"
    webhook_url: "https://your-domain.com/api/channels/yuanbao/webhook"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在元宝应用中找到机器人并发送消息，如果收到 AI 回复则说明接入成功。

## 支持的功能

| 功能 | 支持状态 |
|---|---|
| 文本消息 | ✅ |
| 图片收发 | ✅ |
| 文件收发 | ❌ |
| 流式编辑（打字机效果）| ✅ |
| 输入指示器 | ❌ |
| 表情回应 | ❌ |

## Webhook 配置

在元宝开放平台的机器人配置中设置事件回调地址：

Webhook URL 格式：`https://your-domain.com/api/channels/yuanbao/webhook`

腾讯云 API 请求签名方式：

```python
import hmac, hashlib, time

def sign(secret_key, params):
    string_to_sign = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    signature = hmac.new(
        secret_key.encode(),
        string_to_sign.encode(),
        hashlib.sha1
    ).digest().hex()
    return signature
```

Echo Agent 会自动处理签名验证和请求认证。

## 常见问题

### Q: 腾讯云 API 调用返回签名错误怎么办？
A: 1) 确认 SecretId 和 SecretKey 正确；2) 检查系统时间是否准确（签名包含时间戳）；3) 确认请求参数编码正确；4) 参考腾讯云签名文档排查。

### Q: 元宝机器人无法接收消息怎么办？
A: 1) 确认 Webhook URL 可公网访问且 SSL 证书有效；2) 检查应用 ID 是否正确；3) 确认机器人已发布并通过审核；4) 查看元宝开放平台的事件推送日志。

### Q: 如何使用混元大模型的图片理解功能？
A: 在配置中启用多模态支持，Echo Agent 会自动将用户发送的图片传递给混元多模态模型进行处理。

## 相关链接

- [腾讯云控制台](https://console.cloud.tencent.com/)
- [混元大模型 API 文档](https://cloud.tencent.com/document/product/1729)
- [腾讯云 API 签名文档](https://cloud.tencent.com/document/api/1729/101841)
- [元宝官方网站](https://yuanbao.tencent.com/)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/yuanbao)
