# 钉钉接入指南

## 概述

通过钉钉开放平台的自建机器人将 Echo Agent 接入钉钉，支持在单聊和群聊中与用户进行智能对话。

## 前置条件

- 一个钉钉企业版账号
- 在钉钉开放平台创建应用并获取 AppKey 和 AppSecret
- 拥有钉钉管理后台的应用发布权限

## 5 分钟快速接入

### 1. 获取凭证

1. 访问 [钉钉开放平台](https://open-dev.dingtalk.com/)，点击「创建应用」
2. 选择「企业内部应用」，输入应用名称和描述
3. 在应用详情页获取 AppKey（即 Client ID）和 AppSecret（即 Client Secret）
4. 在「应用能力」中添加「机器人」能力
5. 在「权限管理」中申请以下权限：
   - `im:message` — 获取与发送单聊消息
   - `im:message.group_at_msg` — 获取群聊中 @机器人的消息
   - `im:chat:readonly` — 获取群组信息
6. 在「事件订阅」中配置 HTTP 推送地址并订阅以下事件：
   - `im.message.receive_v1` — 接收消息
7. 发布应用并在管理后台审批通过

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择钉钉，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| Client ID | 钉钉应用的 AppKey | `dingxxxxxxxxxxxxxx` |
| Client Secret | 钉钉应用的 AppSecret | `xxxxxxxxxxxxxxxxxx` |
| Verification URL | 事件订阅验证地址 | `https://your-domain.com/api/channels/dingtalk/webhook` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  dingtalk:
    client_id: "dingxxxxxxxxxxxxxx"
    client_secret: "xxxxxxxxxxxxxxxxxx"
    webhook_url: "https://your-domain.com/api/channels/dingtalk/webhook"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在钉钉中搜索机器人名称并发送私信，或在群聊中 @机器人，如果收到 AI 回复则说明接入成功。

## 支持的功能

| 功能 | 支持状态 |
|---|---|
| 文本消息 | ✅ |
| 图片收发 | ✅ |
| 文件收发 | ✅ |
| 流式编辑（打字机效果）| ✅ |
| 输入指示器 | ❌ |
| 表情回应 | ❌ |

## Webhook 配置

在钉钉开放平台的应用配置中，进入「事件订阅」页面，设置 HTTP 推送地址：

Webhook URL 格式：`https://your-domain.com/api/channels/dingtalk/webhook`

钉钉会向该 URL 发送验证请求，Echo Agent 会自动完成签名验证。

钉钉事件订阅使用 AES 加密事件数据，需在配置中正确设置 `aes_key` 和 `token`（在钉钉开放平台的事件订阅配置中获取）。

## 常见问题

### Q: 机器人无法在群聊中收到消息怎么办？
A: 1) 确认已申请 `im:message.group_at_msg` 权限；2) 确认已订阅 `im.message.receive_v1` 事件；3) 在群聊中 @机器人触发消息；4) 确认机器人已添加到目标群聊。

### Q: 如何发送钉钉互动卡片消息？
A: Echo Agent 会自动将 AI 的结构化输出转换为钉钉互动卡片。如需自定义卡片模板，可在配置中指定 `card_template_id`。

### Q: 钉钉 API 调用频率受限怎么办？
A: 钉钉对企业内部应用有 API 调用频率限制（默认 500 次/分钟）。如需更高配额，可在钉钉开放平台申请提升频率限制。

## 相关链接

- [钉钉开放平台](https://open-dev.dingtalk.com/)
- [钉钉机器人开发指南](https://open.dingtalk.com/document/orgapp/custom-robot-access)
- [钉钉事件订阅文档](https://open.dingtalk.com/document/orgapp/subscribe-to-event-streams)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/dingtalk)
