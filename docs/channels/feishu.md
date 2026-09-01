# 飞书接入指南

## 概述

通过飞书开放平台的自建应用将 Echo Agent 接入飞书，支持在私聊、群组和频道中与用户进行智能对话。

## 前置条件

- 一个飞书企业版账号
- 在飞书开放平台创建自建应用并获取 App ID 和 App Secret
- 拥有飞书管理后台的应用审批权限

## 5 分钟快速接入

### 1. 获取凭证

1. 访问 [飞书开放平台](https://open.feishu.cn/app)，点击「创建企业自建应用」
2. 输入应用名称和描述，点击「创建」
3. 在应用详情页获取 App ID 和 App Secret
4. 在「添加应用能力」中添加「机器人」能力
5. 在「权限管理」中申请以下权限：
   - `im:message` — 获取与发送消息
   - `im:message.group_at_msg` — 接收群聊中 @机器人的消息
   - `im:resource` — 获取消息中的资源文件
   - `im:chat` — 获取群组信息
6. 在「事件订阅」中配置请求地址并订阅以下事件：
   - `im.message.receive_v1` — 接收消息
7. 发布应用版本并在管理后台审批通过

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择飞书，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| App ID | 飞书应用的 App ID | `cli_a5xxxxxxxxxxxxx` |
| App Secret | 飞书应用的 App Secret | `xxxxxxxxxxxxxxxxxx` |
| Verification Token | 事件订阅验证令牌 | `xxxxxxxxxxxxxxxxxx` |
| Encrypt Key | 事件订阅加密密钥 | `xxxxxxxxxxxxxxxxxx` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  feishu:
    app_id: "cli_a5xxxxxxxxxxxxx"
    app_secret: "xxxxxxxxxxxxxxxxxx"
    verification_token: "xxxxxxxxxxxxxxxxxx"
    encrypt_key: "xxxxxxxxxxxxxxxxxx"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在飞书中搜索机器人名称并发送消息，或在群组中 @机器人，如果收到 AI 回复则说明接入成功。

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

在飞书开放平台的应用配置中，进入「事件订阅」页面，设置请求地址：

Webhook URL 格式：`https://your-domain.com/api/channels/feishu/webhook`

飞书会向该 URL 发送 URL 验证请求（包含 `challenge` 字段），Echo Agent 会自动使用 Verification Token 完成验证。

如果启用了加密（推荐），飞书会使用 Encrypt Key 对事件数据进行加密，Echo Agent 会自动解密处理。

## 常见问题

### Q: 事件订阅验证失败怎么办？
A: 1) 确认服务器公网可访问且 SSL 证书有效；2) 确认 Verification Token 与飞书开放平台配置一致；3) 检查服务器是否正确响应 challenge 验证请求。

### Q: 机器人无法在群组中收到消息怎么办？
A: 1) 确认已申请 `im:message.group_at_msg` 权限；2) 确认已订阅 `im.message.receive_v1` 事件；3) 在群组中 @机器人触发消息；4) 确认机器人已添加到目标群组。

### Q: 如何发送飞书卡片消息？
A: Echo Agent 会自动将 AI 的结构化输出转换为飞书交互式卡片。如需自定义卡片模板，可在配置中指定 `card_template_id`。

## 相关链接

- [飞书开放平台](https://open.feishu.cn/)
- [飞书机器人开发指南](https://open.feishu.cn/document/home/develop-a-bot-in-5-minutes/create-an-app)
- [飞书事件订阅文档](https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-guide)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/feishu)
