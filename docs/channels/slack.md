# Slack 接入指南

## 概述

通过 Slack Bolt 框架将 Echo Agent 接入 Slack 工作区，支持在频道、私信和线程中与用户进行智能对话。

## 前置条件

- 一个 Slack 工作区管理员或拥有创建应用权限的账号
- 在 Slack API 网站创建 Slack App 并获取 Bot User OAuth Token
- 公网可访问的服务器（用于接收 Event Subscriptions）

## 5 分钟快速接入

### 1. 获取凭证

1. 访问 [Slack API: Applications](https://api.slack.com/apps)，点击「Create New App」→「From scratch」
2. 输入 App Name 和选择目标工作区
3. 在左侧菜单选择「OAuth & Permissions」，添加以下 Bot Token Scopes：
   - `chat:write` — 发送消息
   - `chat:write.public` — 在未加入的频道发送消息
   - `channels:history` — 读取频道消息
   - `groups:history` — 读取私有频道消息
   - `im:history` — 读取私信消息
   - `files:write` — 上传文件
   - `reactions:write` — 添加表情回应
4. 点击「Install to Workspace」，安装后获取 Bot User OAuth Token（以 `xoxb-` 开头）
5. 在左侧菜单选择「Event Subscriptions」，开启后添加以下事件：
   - `message.channels` — 监听频道消息
   - `message.groups` — 监听私有频道消息
   - `message.im` — 监听私信消息
6. 在「Request URL」中填入 Webhook URL

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 Slack，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| Bot Token | Bot User OAuth Token | `xoxb-1234567890-1234567890-abcdef` |
| Signing Secret | 用于验证请求来源的签名密钥 | `8f742231b10e...` |
| App Token | Socket Mode 令牌（可选） | `xapp-1-A1234567-...` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  slack:
    bot_token: "xoxb-1234567890-1234567890-abcdef"
    signing_secret: "8f742231b10e..."
    app_token: "xapp-1-A1234567-..."
    mode: socket
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在 Slack 中 @提及机器人或发送私信，如果收到 AI 回复则说明接入成功。

## 支持的功能

| 功能 | 支持状态 |
|---|---|
| 文本消息 | ✅ |
| 图片收发 | ✅ |
| 文件收发 | ✅ |
| 流式编辑（打字机效果）| ✅ |
| 输入指示器 | ❌ |
| 表情回应 | ✅ |

## Webhook 配置

Slack 通过 Event Subscriptions 推送事件。在 Slack App 配置页面的「Event Subscriptions」中设置 Request URL：

Webhook URL 格式：`https://your-domain.com/api/channels/slack/events`

Slack 会向该 URL 发送 URL 验证请求（包含 `challenge` 字段），Echo Agent 会自动响应验证。

如果无法提供公网 URL，可使用 Socket Mode（配置 `mode: socket`），通过 WebSocket 连接接收事件，无需暴露端口。

## 常见问题

### Q: 机器人在频道中不响应消息怎么办？
A: 1) 确认机器人已添加到目标频道（在频道中输入 `@机器人名` 邀请）；2) 确认 Event Subscriptions 中已订阅 `message.channels` 事件；3) 确认 Bot Token 拥有 `channels:history` 权限。

### Q: 如何使用 Socket Mode 避免配置公网 URL？
A: 在 Slack App 配置中开启 Socket Mode（需生成 App-Level Token，scope 为 `connections:write`），在 Echo Agent 配置中设置 `mode: socket` 并填入 `app_token`。

### Q: 如何让机器人支持 Slash Command？
A: 在 Slack App 的「Slash Commands」页面创建命令，Request URL 填入 `https://your-domain.com/api/channels/slack/commands`。

## 相关链接

- [Slack API: Applications](https://api.slack.com/apps)
- [Slack Bolt 框架文档](https://slack.dev/bolt-js/tutorial/getting-started)
- [Slack API 事件订阅文档](https://api.slack.com/apis/connections/events-api)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/slack)
