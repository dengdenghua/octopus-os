# WhatsApp 接入指南

## 概述

通过 WhatsApp Business API 或 Evolution API 将 Echo Agent 接入 WhatsApp，支持在私聊和群组中与用户进行智能对话。

## 前置条件

- 一个 WhatsApp Business 账号或通过 Evolution API 桥接个人 WhatsApp
- 如使用 Cloud API：需在 Meta for Developers 创建应用并获取访问令牌
- 如使用 Evolution API：需部署 Evolution API 实例

## 5 分钟快速接入

### 1. 获取凭证

**方式 A：WhatsApp Cloud API（官方）**

1. 访问 [Meta for Developers](https://developers.facebook.com/)，创建一个 Business 应用
2. 添加「WhatsApp」产品，获取临时访问令牌
3. 在「WhatsApp」→「API Setup」中获取 Phone Number ID 和 Business Account ID
4. 配置 Webhook 验证

**方式 B：Evolution API（开源）**

1. 部署 Evolution API：
   ```bash
   docker run -d --name evolution-api \
     -p 8080:8080 \
     -e AUTHENTICATION_API_KEY=your-api-key \
     atendai/evolution-api:latest
   ```
2. 创建实例并获取二维码，使用 WhatsApp 扫码关联

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 WhatsApp，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| 接入方式 | cloud_api 或 evolution | `evolution` |
| Access Token | Cloud API 访问令牌 | `EAAx...` |
| Phone Number ID | Cloud API 电话号码 ID | `123456789012345` |
| Evolution URL | Evolution API 地址 | `http://localhost:8080` |
| Evolution API Key | Evolution API 密钥 | `your-api-key` |
| Instance Name | Evolution 实例名称 | `echo-instance` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  whatsapp:
    provider: evolution
    evolution_url: "http://localhost:8080"
    evolution_api_key: "your-api-key"
    instance_name: "echo-instance"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在 WhatsApp 中向关联的号码发送消息，如果收到 AI 回复则说明接入成功。

## 支持的功能

| 功能 | 支持状态 |
|---|---|
| 文本消息 | ✅ |
| 图片收发 | ✅ |
| 文件收发 | ✅ |
| 流式编辑（打字机效果）| ❌ |
| 输入指示器 | ✅ |
| 表情回应 | ❌ |

## Webhook 配置

**Cloud API 模式：**

在 Meta for Developers 的 WhatsApp 应用配置中设置 Webhook：

Webhook URL 格式：`https://your-domain.com/api/channels/whatsapp/webhook`

验证令牌（Verify Token）需与配置文件中的 `webhook_verify_token` 一致。

**Evolution API 模式：**

Evolution API 通过 WebSocket 或 Webhook 推送消息事件。在创建实例时配置 Webhook URL：

```bash
curl -X POST "http://localhost:8080/instance/create" \
  -H "apikey: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "instanceName": "echo-instance",
    "webhook": {
      "url": "https://your-domain.com/api/channels/whatsapp/webhook",
      "enabled": true
    }
  }'
```

## 常见问题

### Q: Cloud API 的临时令牌过期了怎么办？
A: 临时令牌有效期仅 24 小时。生产环境需使用系统用户令牌（System User Token），在 Meta Business Suite → 用户 → 系统用户中创建，该令牌不会过期。

### Q: Evolution API 扫码后频繁掉线怎么办？
A: 1) 确保 Evolution API 容器资源充足；2) 避免同时在手机上大量操作 WhatsApp；3) 检查网络稳定性；4) 在 Evolution API 配置中启用自动重连。

### Q: 如何发送带按钮的交互式消息？
A: WhatsApp Cloud API 支持交互式消息（Interactive Messages），包括按钮和列表。Echo Agent 会自动将 AI 的结构化输出转换为交互式消息格式。

## 相关链接

- [WhatsApp Cloud API 官方文档](https://developers.facebook.com/docs/whatsapp/cloud-api)
- [Evolution API GitHub](https://github.com/EvolutionAPI/evolution-api)
- [Meta for Developers](https://developers.facebook.com/)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/whatsapp)
