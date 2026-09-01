# 企业微信接入指南

## 概述

通过企业微信自建应用将 Echo Agent 接入企业微信，支持在单聊和群聊中与企业成员进行智能对话。

## 前置条件

- 一个企业微信管理员账号
- 在企业微信管理后台创建自建应用并获取 CorpID、AgentID 和 Secret
- 公网可访问的服务器（用于接收回调事件）

## 5 分钟快速接入

### 1. 获取凭证

1. 登录 [企业微信管理后台](https://work.weixin.qq.com/wework_admin/frame)
2. 在「我的企业」页面获取 CorpID（企业 ID）
3. 在「应用管理」→「自建」中创建应用，获取 AgentID 和 Secret
4. 在应用的「接收消息」设置中配置回调 URL
5. 在「网页授权及 JS-SDK」中设置可信域名

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择企业微信，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| Corp ID | 企业 ID | `ww1234567890abcdef` |
| Agent ID | 应用 AgentID | `1000002` |
| Secret | 应用 Secret | `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| Token | 回调 Token | `your_token` |
| Encoding AES Key | 回调加密密钥 | `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  wecom:
    corp_id: "ww1234567890abcdef"
    agent_id: "1000002"
    secret: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    token: "your_token"
    encoding_aes_key: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在企业微信中找到自建应用并发送消息，如果收到 AI 回复则说明接入成功。

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

在企业微信管理后台的应用配置中，设置「接收消息」的回调 URL：

Webhook URL 格式：`https://your-domain.com/api/channels/wecom/webhook`

企业微信会向该 URL 发送 GET 验证请求（包含 `msg_signature`、`timestamp`、`nonce`、`echostr` 参数），Echo Agent 会自动使用 Token 和 Encoding AES Key 完成验证并返回解密后的 echostr。

## 常见问题

### Q: 回调 URL 验证失败怎么办？
A: 1) 确认服务器公网可访问；2) 确认 Token 和 Encoding AES Key 与管理后台配置一致；3) 检查 Encoding AES Key 是否为 43 位字符串；4) 查看服务器日志确认是否收到验证请求。

### Q: 机器人无法发送消息怎么办？
A: 1) 确认 Secret 正确且应用已发布；2) 检查 access_token 是否有效（有效期 7200 秒）；3) 确认接收消息的用户在应用的可见范围内。

### Q: 如何发送 Markdown 消息？
A: 企业微信支持 Markdown 格式消息（msgtype 为 `markdown`），Echo Agent 会自动将 AI 回复转换为 Markdown 格式发送。注意企业微信的 Markdown 支持有限，仅支持部分语法。

## 相关链接

- [企业微信管理后台](https://work.weixin.qq.com/wework_admin/frame)
- [企业微信 API 文档](https://developer.work.weixin.qq.com/document/)
- [企业微信回调配置指南](https://developer.work.weixin.qq.com/document/path/90930)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/wecom)
