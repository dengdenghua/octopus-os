# LINE 接入指南

## 概述

通过 LINE Messaging API 将 Echo Agent 接入 LINE，支持在私聊和群组中与用户进行智能对话。

## 前置条件

- 一个 LINE 账号
- 在 LINE Developers Console 创建 Provider 和 Messaging API Channel
- 公网可访问的服务器（用于接收 Webhook）

## 5 分钟快速接入

### 1. 获取凭证

1. 访问 [LINE Developers Console](https://developers.line.biz/console/)，登录 LINE 账号
2. 创建一个 Provider（或使用已有的）
3. 在 Provider 下创建 Messaging API Channel
4. 在 Channel 设置页面获取：
   - Channel ID
   - Channel Secret
   - Channel Access Token（点击「Issue」生成）
5. 在「Messaging API」设置中：
   - 启用「Use webhook」
   - 关闭「Auto-reply messages」（避免与 AI 回复冲突）
   - 关闭「Greeting messages」

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 LINE，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| Channel Secret | 频道密钥 | `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| Channel Access Token | 频道访问令牌 | `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx...` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  line:
    channel_secret: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    channel_access_token: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx..."
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在 LINE 中搜索机器人名称并发送消息，或在群组中 @提及机器人，如果收到 AI 回复则说明接入成功。

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

在 LINE Developers Console 的 Channel 设置中，进入「Messaging API」页面，设置 Webhook URL：

Webhook URL 格式：`https://your-domain.com/api/channels/line/webhook`

点击「Verify」验证连接。LINE 会使用 Channel Secret 对请求进行签名验证，Echo Agent 会自动校验 `X-Line-Signature` 头。

注意：LINE 的 Channel Access Token 长期有效但可能过期，建议在配置中设置自动刷新：

```yaml
channels:
  line:
    channel_secret: "..."
    channel_access_token: "..."
    auto_refresh_token: true
```

## 常见问题

### Q: Webhook 验证失败怎么办？
A: 1) 确认服务器公网可访问且 SSL 证书有效（LINE 要求 HTTPS）；2) 确认 Channel Secret 配置正确；3) 检查服务器是否正确响应 200 状态码；4) 查看 LINE Developers Console 中的 Webhook 发送日志。

### Q: 机器人在群组中无法收到消息怎么办？
A: LINE 群组中的机器人默认只能收到 @提及的消息。如需接收所有消息，需在 LINE Developers Console 中申请「Message Read」权限（需审核）。

### Q: 如何发送 Flex Message？
A: Echo Agent 会自动将 AI 的结构化输出转换为 LINE Flex Message 格式。如需自定义 Flex Message 模板，可在配置中指定 `flex_template`。

## 相关链接

- [LINE Developers Console](https://developers.line.biz/console/)
- [LINE Messaging API 文档](https://developers.line.biz/en/docs/messaging-api/)
- [LINE Flex Message 模拟器](https://developers.line.biz/flex-simulator/)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/line)
