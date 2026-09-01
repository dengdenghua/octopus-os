# ntfy 接入指南

## 概述

通过 ntfy 的订阅机制将 Echo Agent 接入通知推送通道，实现基于 HTTP 的轻量级消息收发与 AI 对话。

## 前置条件

- 一个 ntfy 服务器（可使用公共服务器 ntfy.sh 或自建）
- 了解 ntfy 的发布/订阅模型
- 服务器需能访问 ntfy 服务器

## 5 分钟快速接入

### 1. 获取凭证

ntfy 采用基于主题（Topic）的发布/订阅模型，无需注册账号即可使用。

1. 选择一个主题名称（如 `echo-agent-xxx`），建议使用随机字符串避免冲突
2. （可选）在自建 ntfy 服务器上配置访问控制
3. （可选）为敏感主题设置密码保护

自建 ntfy 服务器：

```bash
docker run -d --name ntfy \
  -p 80:80 \
  -v /var/lib/ntfy:/var/lib/ntfy \
  binwiederhier/ntfy serve
```

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 ntfy，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| 服务器地址 | ntfy 服务器 URL | `https://ntfy.sh` |
| 订阅主题 | 接收用户消息的主题 | `echo-agent-in` |
| 发布主题 | 发送 AI 回复的主题 | `echo-agent-out` |
| 认证用户名 | 访问控制用户名（可选） | `admin` |
| 认证密码 | 访问控制密码（可选） | `xxxxxxxx` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  ntfy:
    server_url: "https://ntfy.sh"
    subscribe_topic: "echo-agent-in"
    publish_topic: "echo-agent-out"
    auth_username: "admin"
    auth_password: "xxxxxxxx"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

向订阅主题发送测试消息：

```bash
curl -d "你好" "https://ntfy.sh/echo-agent-in"
```

检查发布主题是否收到 AI 回复：

```bash
curl "https://ntfy.sh/echo-agent-out/json"
```

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

ntfy 支持通过订阅的 Webhook 回调推送消息。Echo Agent 默认使用长轮询（`/subscribe`）方式接收消息。

如需使用 Webhook 模式，在 ntfy 服务器配置中设置 UnifiedPush 或 Webhook 转发：

Webhook URL 格式：`https://your-domain.com/api/channels/ntfy/webhook`

ntfy 订阅方式对比：

| 方式 | 延迟 | 适用场景 |
|---|---|---|
| 长轮询（默认） | 低 | 服务器部署 |
| WebSocket | 最低 | 实时性要求高 |
| Webhook | 中 | 无法主动连接 ntfy 服务器 |

配置 WebSocket 模式：

```yaml
channels:
  ntfy:
    server_url: "https://ntfy.sh"
    subscribe_topic: "echo-agent-in"
    publish_topic: "echo-agent-out"
    mode: websocket
```

## 常见问题

### Q: 使用公共 ntfy.sh 服务器安全吗？
A: 公共服务器上的主题是公开的，任何知道主题名的人都可以订阅。建议：1) 使用随机且难以猜测的主题名；2) 自建 ntfy 服务器并启用访问控制；3) 为主题设置密码保护。

### Q: 消息丢失怎么办？
A: 1) 确认 Echo Agent 持续订阅主题；2) 在 ntfy 服务器配置中启用消息缓存（`cache-duration`）；3) 使用 `since: all` 参数获取历史消息。

### Q: 如何限制谁可以向机器人发送消息？
A: 在自建 ntfy 服务器的配置文件中设置访问控制规则：

```
# server.yml
auth-file: /var/lib/ntfy/user.db
auth-default-access: deny-all

# 允许认证用户向输入主题发布
- topic: "echo-agent-in"
  write: ["user1", "user2"]
```

## 相关链接

- [ntfy 官方网站](https://ntfy.sh/)
- [ntfy GitHub](https://github.com/binwiederhier/ntfy)
- [ntfy API 文档](https://docs.ntfy.sh/publish/)
- [ntfy 自建服务器指南](https://docs.ntfy.sh/config/)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/ntfy)
