# Matrix 接入指南

## 概述

通过 Matrix 协议将 Echo Agent 接入 Matrix 网络，支持在房间和私信中与用户进行去中心化的智能对话。

## 前置条件

- 一个 Matrix 账号（可注册在任意 Homeserver 上）
- 选择一个 Homeserver（如 matrix.org 或自建 Synapse/Dendrite）
- 了解 Matrix 用户 ID 格式：`@username:homeserver.tld`

## 5 分钟快速接入

### 1. 获取凭证

1. 在目标 Homeserver 上注册一个专用账号（如 `@echo-bot:matrix.org`）
2. 获取 Access Token：
   - 方式 A：在 Element 客户端中，设置 → 帮助与关于 → 高级 → 访问令牌
   - 方式 B：通过 API 获取：
     ```bash
     curl -X POST "https://matrix.org/_matrix/client/v3/login" \
       -H "Content-Type: application/json" \
       -d '{
         "type": "m.login.password",
         "identifier": {"type": "m.id.user", "user": "echo-bot"},
         "password": "your-password"
       }'
     ```
3. 记录返回的 `access_token`

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 Matrix，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| Homeserver URL | Matrix 服务器地址 | `https://matrix.org` |
| User ID | 机器人的 Matrix ID | `@echo-bot:matrix.org` |
| Access Token | 登录访问令牌 | `syt_xxxxxxxxxxxxx_xxxxxxxxxxxxx` |
| 设备 ID | 设备标识（可选） | `ECHO01` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  matrix:
    homeserver_url: "https://matrix.org"
    user_id: "@echo-bot:matrix.org"
    access_token: "syt_xxxxxxxxxxxxx_xxxxxxxxxxxxx"
    device_id: "ECHO01"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在 Matrix 客户端（如 Element）中向机器人发送私信或在房间中 @提及机器人，如果收到 AI 回复则说明接入成功。

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

Matrix 使用长轮询（`/sync`）方式接收事件，无需配置 Webhook URL。Echo Agent 启动后会自动连接 Homeserver 并通过 `/sync` 接口监听新消息事件。

如需使用应用服务（Application Service）模式实现更高性能的事件接收，需在 Homeserver 中注册应用服务并配置 `as_token`：

```yaml
channels:
  matrix:
    homeserver_url: "https://matrix.org"
    mode: appservice
    as_token: "your-appservice-token"
    hs_token: "your-homeserver-token"
```

应用服务 Webhook URL 格式：`https://your-domain.com/api/channels/matrix/appservice/_matrix/app/v1/transactions/{txnId}`

## 常见问题

### Q: 机器人无法接收消息怎么办？
A: 1) 确认 Access Token 有效（可在 Element 中重新获取）；2) 确认机器人已加入目标房间；3) 检查 Homeserver 的 `/sync` 连接是否正常；4) 查看服务器日志中的 Matrix 同步错误。

### Q: 如何让机器人自动加入被邀请的房间？
A: Echo Agent 默认自动接受房间邀请。如需关闭此行为，在配置中设置 `auto_join_rooms: false`。

### Q: 如何在自建 Homeserver 上部署？
A: 推荐使用 Synapse 或 Dendrite 作为 Homeserver。部署后需配置 `homeserver_url` 为本地地址（如 `http://localhost:8008`），并确保机器人账号已在本地注册。

## 相关链接

- [Matrix 官方网站](https://matrix.org/)
- [Matrix Client-Server API 文档](https://spec.matrix.org/v1.9/client-server-api/)
- [Synapse Homeserver](https://github.com/element-hq/synapse)
- [Element 客户端](https://element.io/)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/matrix)
