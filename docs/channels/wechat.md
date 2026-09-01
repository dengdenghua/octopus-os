# 微信接入指南

## 概述

通过微信公众号/微信客服接口将 Echo Agent 接入微信生态，实现与用户在微信公众号和微信客服中的智能对话。

## 前置条件

- 一个微信公众平台的公众号（服务号）或微信客服账号
- 公众号需通过微信认证
- 公网可访问的服务器（用于接收微信回调）
- 了解微信公众号开发模式

## 5 分钟快速接入

### 1. 获取凭证

1. 登录 [微信公众平台](https://mp.weixin.qq.com/)
2. 在「设置与开发」→「基本配置」中获取：
   - AppID（开发者ID）
   - AppSecret（开发者密码）
3. 在「设置与开发」→「基本配置」→「服务器配置」中设置：
   - URL：Webhook 回调地址
   - Token：自定义令牌
   - EncodingAESKey：消息加密密钥
   - 消息加解密方式：安全模式（推荐）
4. 启用服务器配置（注意：启用后自动回复和自定义菜单将失效，需通过 API 实现）

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择微信，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| App ID | 公众号 AppID | `wx1234567890abcdef` |
| App Secret | 公众号 AppSecret | `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| Token | 服务器配置 Token | `your_token` |
| Encoding AES Key | 消息加密密钥 | `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  wechat:
    app_id: "wx1234567890abcdef"
    app_secret: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    token: "your_token"
    encoding_aes_key: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在微信中关注公众号并发送消息，如果收到 AI 回复则说明接入成功。

## 支持的功能

| 功能 | 支持状态 |
|---|---|
| 文本消息 | ✅ |
| 图片收发 | ✅ |
| 文件收发 | ❌ |
| 流式编辑（打字机效果）| ❌ |
| 输入指示器 | ❌ |
| 表情回应 | ❌ |

## Webhook 配置

在微信公众平台的「设置与开发」→「基本配置」→「服务器配置」中设置回调 URL：

Webhook URL 格式：`https://your-domain.com/api/channels/wechat/webhook`

微信会向该 URL 发送 GET 验证请求（包含 `signature`、`timestamp`、`nonce`、`echostr` 参数），Echo Agent 会自动使用 Token 完成签名验证并返回 echostr。

微信消息加解密说明：

- **明文模式**：消息不加密，安全性低
- **兼容模式**：明文和密文同时存在
- **安全模式**（推荐）：消息使用 AES 加密，EncodingAESKey 为 43 位字符串

## 常见问题

### Q: 服务器配置验证失败怎么办？
A: 1) 确认服务器公网可访问且支持 80/443 端口；2) 确认 Token 与公众平台配置一致；3) 检查签名计算逻辑是否正确（SHA1(sort(token, timestamp, nonce))）；4) 确认服务器正确返回 echostr。

### Q: 公众号 5 秒无响应超时怎么办？
A: 微信公众号要求服务器在 5 秒内返回响应。如果 AI 生成时间超过 5 秒：1) 先返回「success」字符串（避免微信重试）；2) 使用客服消息接口异步发送回复；3) 在配置中设置 `use_customer_service: true`。

### Q: 如何发送图文消息？
A: Echo Agent 会自动将 AI 的结构化输出转换为微信图文消息格式。如需自定义，可在配置中指定 `news_template`。注意微信图文消息有条数限制（最多 8 条）。

### Q: 订阅号可以使用吗？
A: 订阅号功能受限较多（每天只能群发 1 条消息，无法使用客服消息接口）。建议使用认证的服务号以获得完整功能支持。

## 相关链接

- [微信公众平台](https://mp.weixin.qq.com/)
- [微信公众号开发文档](https://developers.weixin.qq.com/doc/offiaccount/Getting_Started/Overview.html)
- [微信客服消息接口](https://developers.weixin.qq.com/doc/offiaccount/Message_Management/Service_Center_messages.html)
- [微信消息加解密说明](https://developers.weixin.qq.com/doc/offiaccount/Message_Management/Message_Encryption_and_Decryption_Instructions.html)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/wechat)
