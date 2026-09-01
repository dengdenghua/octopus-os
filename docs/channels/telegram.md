# Telegram 接入指南

## 概述

通过 Telegram Bot API 将 Echo Agent 接入 Telegram，实现与用户在私聊和群组中的智能对话。

## 前置条件

- 一个 Telegram 账号
- 在 Telegram 中与 @BotFather 交互创建机器人并获取 Bot Token
- 一个公网可访问的服务器（用于接收 Webhook）或使用长轮询模式

## 5 分钟快速接入

### 1. 获取凭证

1. 在 Telegram 中搜索 `@BotFather` 并发送 `/newbot`
2. 按提示输入机器人名称（显示名）和用户名（必须以 `bot` 结尾，如 `my_echo_bot`）
3. BotFather 会返回 Bot Token，格式如 `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`
4. （可选）发送 `/setprivacy` 选择 `Disable` 以允许机器人在群组中接收所有消息

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 Telegram，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| Bot Token | BotFather 颁发的机器人令牌 | `123456789:ABCdefGHIjklMNOpqrsTUVwxyz` |
| Webhook URL | 接收消息的公网地址 | `https://your-domain.com/api/channels/telegram/webhook` |
| 模式 | Webhook 或长轮询 | `webhook` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  telegram:
    bot_token: "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
    mode: webhook
    webhook_url: "https://your-domain.com/api/channels/telegram/webhook"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在 Telegram 中找到你创建的机器人，发送 `/start` 或任意文本消息，如果收到 AI 回复则说明接入成功。

## 支持的功能

| 功能 | 支持状态 |
|---|---|
| 文本消息 | ✅ |
| 图片收发 | ✅ |
| 文件收发 | ✅ |
| 流式编辑（打字机效果）| ✅ |
| 输入指示器 | ✅ |
| 表情回应 | ✅ |

## Webhook 配置

使用 Webhook 模式时，Echo Agent 启动后会自动调用 Telegram 的 `setWebhook` 接口注册回调地址。

手动设置 Webhook：

```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-domain.com/api/channels/telegram/webhook"}'
```

Webhook URL 格式：`https://your-domain.com/api/channels/telegram/webhook`

验证 Webhook 状态：

```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

## 常见问题

### Q: 机器人在群组中无法收到消息怎么办？
A: 默认情况下 Telegram Bot 的隐私模式开启，只能收到 `/` 命令和 @提及。在 BotFather 中发送 `/setprivacy`，选择你的机器人，然后选择 `Disable` 关闭隐私模式。

### Q: Webhook 无法接收消息怎么排查？
A: 1) 确认服务器有公网 IP 且 SSL 证书有效（Telegram 要求 HTTPS）；2) 使用 `getWebhookInfo` 查看最近错误信息；3) 检查防火墙是否放行 443 端口；4) 如无公网环境，可切换为长轮询模式（`mode: polling`）。

### Q: 如何发送 Markdown 格式的消息？
A: Echo Agent 默认使用 MarkdownV2 格式发送消息。如需切换，可在配置中设置 `parse_mode: HTML` 或 `parse_mode: Markdown`。

## 相关链接

- [Telegram Bot API 官方文档](https://core.telegram.org/bots/api)
- [BotFather 使用指南](https://core.telegram.org/bots/features#botfather)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/telegram)
