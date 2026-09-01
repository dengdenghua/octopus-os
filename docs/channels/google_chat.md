# Google Chat 接入指南

## 概述

通过 Google Chat API 将 Echo Agent 接入 Google Chat，支持在聊天室和私信中与 Google Workspace 用户进行智能对话。

## 前置条件

- 一个 Google Workspace 账号
- 在 Google Cloud Console 创建项目并启用 Chat API
- 拥有 Google Workspace 管理员权限（用于发布应用）

## 5 分钟快速接入

### 1. 获取凭证

1. 访问 [Google Cloud Console](https://console.cloud.google.com/)，创建新项目
2. 在「API 和服务」→「库」中搜索并启用「Google Chat API」
3. 在「API 和服务」→「凭据」中创建服务账号：
   - 点击「创建凭据」→「服务账号」
   - 输入服务账号名称，点击「创建并继续」
   - 记录服务账号邮箱地址
4. 为服务账号创建 JSON 密钥文件并下载
5. 在「Google Chat API」→「配置」中设置：
   - 应用名称和头像
   - 功能选择：「接收 1:1 消息」和「加入群聊和聊天室」
   - 连接设置：选择「HTTP 端点」并填入 Webhook URL

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 Google Chat，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| 服务账号密钥 | JSON 密钥文件路径或内容 | `/path/to/service-account.json` |
| 项目 ID | Google Cloud 项目 ID | `my-echo-project` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  google_chat:
    service_account_key: "/path/to/service-account.json"
    project_id: "my-echo-project"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在 Google Chat 中搜索机器人名称并发起私信，或在聊天室中 @提及机器人，如果收到 AI 回复则说明接入成功。

## 支持的功能

| 功能 | 支持状态 |
|---|---|
| 文本消息 | ✅ |
| 图片收发 | ✅ |
| 文件收发 | ❌ |
| 流式编辑（打字机效果）| ❌ |
| 输入指示器 | ❌ |
| 表情回应 | ✅ |

## Webhook 配置

在 Google Cloud Console 的 Google Chat API 配置中，设置 HTTP 端点：

Webhook URL 格式：`https://your-domain.com/api/channels/google_chat/webhook`

Google Chat 会向该 URL 发送事件通知（包括消息事件、添加到聊天室事件等）。

验证请求：Google Chat 会在请求头中附带 Bearer Token，Echo Agent 会自动验证请求来源。

## 常见问题

### Q: 机器人无法接收消息怎么办？
A: 1) 确认 Google Chat API 已启用且配置正确；2) 确认 HTTP 端点 URL 可公网访问；3) 检查服务账号权限；4) 在 Google Chat API 配置中确认已启用「接收 1:1 消息」功能。

### Q: 如何发送卡片消息？
A: Echo Agent 会自动将 AI 的结构化输出转换为 Google Chat Card 格式。如需自定义卡片，可在配置中指定 `card_template`。

### Q: 非 Google Workspace 用户可以使用吗？
A: Google Chat API 仅支持 Google Workspace 组织内的用户。个人 Gmail 账号无法使用 Chat Bot 功能。

## 相关链接

- [Google Chat API 文档](https://developers.google.com/chat/api/guides)
- [Google Cloud Console](https://console.cloud.google.com/)
- [Google Chat Bot 开发指南](https://developers.google.com/chat/how-tos/bot-develop)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/google_chat)
