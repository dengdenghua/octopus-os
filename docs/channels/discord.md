# Discord 接入指南

## 概述

通过 Discord Bot 将 Echo Agent 接入 Discord 服务器，支持在频道和私信中与用户进行智能对话。

## 前置条件

- 一个 Discord 账号
- 在 Discord Developer Portal 创建应用并获取 Bot Token
- 拥有目标 Discord 服务器的管理权限（用于邀请机器人）

## 5 分钟快速接入

### 1. 获取凭证

1. 访问 [Discord Developer Portal](https://discord.com/developers/applications)，点击「New Application」
2. 输入应用名称，点击「Create」
3. 在左侧菜单选择「Bot」，点击「Reset Token」获取 Bot Token
4. 在「Bot」页面开启以下 Privileged Gateway Intents：
   - Message Content Intent
   - Server Members Intent（可选）
5. 在左侧菜单选择「OAuth2」→「URL Generator」，勾选以下权限：
   - Scopes: `bot`
   - Bot Permissions: `Send Messages`、`Read Message History`、`Add Reactions`、`Attach Files`、`Use Slash Commands`
6. 复制生成的邀请链接，在浏览器中打开并选择目标服务器添加机器人

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 Discord，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| Bot Token | Discord Developer Portal 颁发的机器人令牌 | `MTIzNDU2Nzg5MDEy...` |
| Application ID | Discord 应用的 Client ID | `1234567890123456789` |
| 响应模式 | 回复或追加 | `reply` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  discord:
    bot_token: "MTIzNDU2Nzg5MDEy..."
    application_id: "1234567890123456789"
    response_mode: reply
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在 Discord 服务器中 @提及机器人或发送私信，如果收到 AI 回复则说明接入成功。

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

Discord 使用 WebSocket Gateway 接收事件，无需额外配置 Webhook URL。Echo Agent 启动后会自动连接 Discord Gateway 并监听消息事件。

如需接收 Slash Command 交互事件，需在 Developer Portal 中注册 Slash Command 并配置 Interaction Endpoint URL：

Webhook URL 格式：`https://your-domain.com/api/channels/discord/interactions`

## 常见问题

### Q: 机器人无法读取消息内容怎么办？
A: 在 Discord Developer Portal → Bot 页面，确保开启了「Message Content Intent」。未开启此 Intent 时，机器人只能收到消息的元数据，无法获取实际内容。

### Q: 机器人无法加入服务器怎么办？
A: 确认生成的 OAuth2 邀请链接中包含了 `bot` Scope 和必要的权限。同时确保你的 Discord 账号在目标服务器中拥有「管理服务器」权限。

### Q: 如何限制机器人只在特定频道响应？
A: 在配置文件中添加 `allowed_channels` 字段：

```yaml
channels:
  discord:
    bot_token: "..."
    allowed_channels:
      - "1234567890123456789"
```

## 相关链接

- [Discord Developer Portal](https://discord.com/developers/applications)
- [Discord API 官方文档](https://discord.com/developers/docs/intro)
- [Discord.js 官方文档](https://discord.js.org/)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/discord)
