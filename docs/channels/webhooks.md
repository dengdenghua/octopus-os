# Webhooks 接入指南

## 概述

通过通用 Webhook 接口将 Echo Agent 接入任意支持 HTTP 回调的系统，实现灵活的消息收发与 AI 对话集成。

## 前置条件

- 一个公网可访问的服务器（用于接收 Webhook 回调）
- 了解 HTTP 请求和 JSON 数据格式
- 目标系统支持发送 HTTP Webhook 回调

## 5 分钟快速接入

### 1. 获取凭证

Webhooks 渠道无需注册第三方平台账号。只需生成一个 Webhook 签名密钥用于验证请求来源：

1. 在 Echo Agent Web UI 中创建 Webhook 渠道
2. 系统自动生成 Webhook URL 和签名密钥
3. 记录 Webhook URL 和签名密钥

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 Webhooks，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| Webhook 路径 | 自定义 Webhook 路径 | `my-custom-hook` |
| 签名密钥 | 用于验证请求的密钥 | `whsec_xxxxxxxxxxxxx` |
| 消息格式 | 请求体的消息字段路径 | `data.message` |
| 用户标识字段 | 请求体的用户标识路径 | `data.user_id` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  webhooks:
    hooks:
      - path: "my-custom-hook"
        secret: "whsec_xxxxxxxxxxxxx"
        message_path: "data.message"
        user_id_path: "data.user_id"
        conversation_id_path: "data.conversation_id"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

向 Webhook URL 发送测试请求：

```bash
curl -X POST "https://your-domain.com/api/channels/webhooks/my-custom-hook" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Signature: sha256=xxxxxxxxxxxx" \
  -d '{
    "data": {
      "message": "你好",
      "user_id": "user123",
      "conversation_id": "conv456"
    }
  }'
```

检查是否收到 AI 回复响应。

## 支持的功能

| 功能 | 支持状态 |
|---|---|
| 文本消息 | ✅ |
| 图片收发 | ✅ |
| 文件收发 | ✅ |
| 流式编辑（打字机效果）| ❌ |
| 输入指示器 | ❌ |
| 表情回应 | ❌ |

## Webhook 配置

Webhook URL 格式：`https://your-domain.com/api/channels/webhooks/{hook_path}`

### 请求格式

Echo Agent 接收标准 HTTP POST 请求，请求体为 JSON 格式：

```json
{
  "data": {
    "message": "用户消息内容",
    "user_id": "唯一用户标识",
    "conversation_id": "会话标识（可选）",
    "metadata": {
      "key": "value"
    }
  }
}
```

### 响应格式

AI 回复通过 HTTP 响应返回：

```json
{
  "reply": "AI 回复内容",
  "conversation_id": "会话标识",
  "metadata": {}
}
```

### 签名验证

为防止伪造请求，建议启用签名验证。Echo Agent 使用 HMAC-SHA256 签名：

```
X-Webhook-Signature: sha256=<hmac_hex>
```

签名计算方式：

```python
import hmac, hashlib
signature = hmac.new(
    secret.encode(),
    request_body.encode(),
    hashlib.sha256
).hexdigest()
header = f"sha256={signature}"
```

### 回调 Webhook

如需异步回复（而非同步 HTTP 响应），可配置回调 Webhook URL：

```yaml
channels:
  webhooks:
    hooks:
      - path: "my-custom-hook"
        secret: "whsec_xxxxxxxxxxxxx"
        callback_url: "https://target-system.com/api/receive"
        callback_headers:
          Authorization: "Bearer xxxxxxxx"
```

## 常见问题

### Q: 如何与 GitHub/GitLab 等平台集成？
A: 在 GitHub/GitLab 的 Webhook 设置中，将 Payload URL 填入 Echo Agent 的 Webhook URL。需自定义 `message_path` 以匹配平台的事件格式，如 GitHub Issues 事件使用 `issue.body` 作为消息字段。

### Q: 如何处理多个不同的 Webhook 来源？
A: 在配置中定义多个 hook，每个 hook 使用不同的 path 和消息格式映射：

```yaml
channels:
  webhooks:
    hooks:
      - path: "github"
        message_path: "issue.body"
        user_id_path: "sender.login"
      - path: "gitlab"
        message_path: "object_attributes.description"
        user_id_path: "user.username"
```

### Q: 请求超时怎么办？
A: AI 生成回复可能需要较长时间。如果调用方超时，建议使用异步回调模式（配置 `callback_url`），Echo Agent 会先返回 202 状态码，生成完成后主动推送回复。

## 相关链接

- [Webhook 安全最佳实践](https://developer.github.com/webhooks/securing/)
- [Echo Agent Webhook API 文档](https://docs.echo-agent.dev/api/webhooks)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/webhooks)
