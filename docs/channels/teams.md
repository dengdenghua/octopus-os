# Microsoft Teams 接入指南

## 概述

通过 Microsoft Bot Framework 将 Echo Agent 接入 Microsoft Teams，支持在频道、群聊和私信中与用户进行智能对话。

## 前置条件

- 一个 Microsoft 365 开发者账号
- 在 Azure 门户注册应用并获取 App ID 和 App Password
- 拥有 Teams 管理员权限（用于上传自定义应用）

## 5 分钟快速接入

### 1. 获取凭证

1. 访问 [Azure 门户](https://portal.azure.com/)，在「Azure Active Directory」→「应用注册」中注册新应用
2. 记录「应用程序(客户端) ID」
3. 在「证书和密码」中创建新的客户端密码，记录值
4. 在「API 权限」中添加以下 Microsoft Graph 权限：
   - `Chat.Read` — 读取聊天消息
   - `ChatMessage.Send` — 发送聊天消息
   - `ChannelMessage.Read.All` — 读取频道消息
5. 访问 [Bot Framework Portal](https://dev.botframework.com/)，创建 Bot 并关联 Azure 应用
6. 在 Bot 设置中配置 Messaging Endpoint

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 Teams，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| App ID | Azure 应用的客户端 ID | `12345678-1234-1234-1234-123456789012` |
| App Password | Azure 应用的客户端密码 | `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| Tenant ID | Azure AD 租户 ID（可选） | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  teams:
    app_id: "12345678-1234-1234-1234-123456789012"
    app_password: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    tenant_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在 Teams 中搜索机器人名称并发送消息，如果收到 AI 回复则说明接入成功。开发阶段可使用 [Bot Framework Emulator](https://github.com/microsoft/BotFramework-Emulator) 进行本地测试。

## 支持的功能

| 功能 | 支持状态 |
|---|---|
| 文本消息 | ✅ |
| 图片收发 | ✅ |
| 文件收发 | ✅ |
| 流式编辑（打字机效果）| ✅ |
| 输入指示器 | ✅ |
| 表情回应 | ❌ |

## Webhook 配置

Teams 通过 Bot Framework 的 Messaging Endpoint 接收事件。在 Bot Framework Portal 的 Bot 设置中配置：

Webhook URL 格式：`https://your-domain.com/api/channels/teams/messages`

确保该 URL 使用 HTTPS 且 SSL 证书有效。

在 Teams 中安装机器人：

1. 创建 Teams 应用清单（manifest.json）
2. 打包为 .zip 文件
3. 在 Teams 的「应用」→「管理应用」→「上传自定义应用」中上传

## 常见问题

### Q: 机器人无法接收消息怎么办？
A: 1) 确认 Messaging Endpoint URL 可公网访问；2) 检查 App ID 和 App Password 是否正确；3) 确认机器人已安装到目标 Teams 环境；4) 使用 Bot Framework Emulator 测试连接。

### Q: 如何在 Teams 中上传自定义应用？
A: 需要 Teams 管理员在「Teams 管理中心」→「Teams 应用」→「权限策略」中允许上传自定义应用。开发阶段可在「开发人员预览」模式下侧载应用。

### Q: 如何发送 Adaptive Card？
A: Echo Agent 会自动将 AI 的结构化输出转换为 Teams Adaptive Card 格式。如需自定义卡片，可在配置中指定 `adaptive_card_template`。

## 相关链接

- [Microsoft Bot Framework 文档](https://learn.microsoft.com/azure/bot-service/)
- [Teams Bot 开发指南](https://learn.microsoft.com/microsoftteams/platform/bots/what-are-bots)
- [Azure 门户](https://portal.azure.com/)
- [Bot Framework Emulator](https://github.com/microsoft/BotFramework-Emulator)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/teams)
