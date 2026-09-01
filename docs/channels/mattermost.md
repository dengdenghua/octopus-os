# Mattermost 接入指南

## 概述

通过 Mattermost Bot API 将 Echo Agent 接入 Mattermost 工作区，支持在频道和私信中与用户进行智能对话。

## 前置条件

- 一个 Mattermost 服务器（自建或云托管）
- 拥有 Mattermost 系统管理员权限
- 服务器版本 7.0 及以上

## 5 分钟快速接入

### 1. 获取凭证

1. 登录 Mattermost 服务器，进入「系统控制台」→「集成」→「Bot 账户」
2. 点击「添加 Bot 账户」，输入机器人名称和描述
3. 选择角色为「Bot」，点击「创建」
4. 复制生成的 Bot Token（以 `token` 开头的字符串）
5. 在「系统控制台」→「集成」→「Webhook 和命令」中确认启用了以下功能：
   - 启用传入 Webhook
   - 启用传出 Webhook
   - 启用斜杠命令

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 Mattermost，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| 服务器 URL | Mattermost 服务器地址 | `https://mattermost.example.com` |
| Bot Token | 机器人访问令牌 | `token1xxxxxxxxxxxxxxxxxxxxxxxxxx` |
| Team 名称 | 默认团队名称 | `engineering` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  mattermost:
    server_url: "https://mattermost.example.com"
    bot_token: "token1xxxxxxxxxxxxxxxxxxxxxxxxxx"
    team_name: "engineering"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在 Mattermost 中 @提及机器人或发送私信，如果收到 AI 回复则说明接入成功。

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

Mattermost 使用 WebSocket 连接接收实时事件，Echo Agent 启动后会自动通过 WebSocket 监听消息事件，无需额外配置 Webhook URL。

如需使用 Webhook 模式，可在 Mattermost 中创建传出 Webhook：

1. 在「集成」→「传出 Webhook」中创建新 Webhook
2. 设置触发频道和触发词
3. 设置回调 URL：

Webhook URL 格式：`https://your-domain.com/api/channels/mattermost/webhook`

## 常见问题

### Q: 机器人无法接收消息怎么办？
A: 1) 确认 Bot Token 有效；2) 确认机器人已加入目标频道（在频道中输入 `@机器人名` 邀请）；3) 检查 WebSocket 连接是否正常；4) 查看 Mattermost 服务器日志。

### Q: 如何限制机器人只在特定频道响应？
A: 在配置文件中添加 `allowed_channels` 字段：

```yaml
channels:
  mattermost:
    server_url: "https://mattermost.example.com"
    bot_token: "..."
    allowed_channels:
      - "town-square"
      - "ai-assistant"
```

### Q: 如何配置斜杠命令？
A: 在 Mattermost 的「集成」→「斜杠命令」中创建命令，Request URL 填入 `https://your-domain.com/api/channels/mattermost/slash`，Request Method 选择 `POST`。

## 相关链接

- [Mattermost 官方网站](https://mattermost.com/)
- [Mattermost Bot 开发文档](https://developers.mattermost.com/integrate/apps/bot-accounts/)
- [Mattermost API 参考](https://api.mattermost.com/)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/mattermost)
