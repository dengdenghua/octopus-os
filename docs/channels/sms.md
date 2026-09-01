# SMS 接入指南

## 概述

通过短信网关 API 将 Echo Agent 接入短信通道，实现通过手机短信与 AI 进行对话交互。

## 前置条件

- 一个短信服务提供商账号（如阿里云短信、腾讯云短信、Twilio 等）
- 获取短信服务的 API Key 和签名
- 一个已审核通过的短信签名和模板（国内短信需备案）

## 5 分钟快速接入

### 1. 获取凭证

**阿里云短信：**

1. 访问 [阿里云短信服务](https://www.aliyun.com/product/sms)，开通服务
2. 在「国内消息」中添加签名和模板，等待审核通过
3. 在 AccessKey 管理中创建 AccessKey ID 和 AccessKey Secret

**腾讯云短信：**

1. 访问 [腾讯云短信服务](https://cloud.tencent.com/product/sms)，开通服务
2. 创建应用，获取 SDK AppID 和 App Key
3. 添加签名和模板，等待审核通过

**Twilio（国际短信）：**

1. 访问 [Twilio](https://www.twilio.com/)，注册账号
2. 获取 Account SID 和 Auth Token
3. 购买一个支持短信的 Twilio 电话号码

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 SMS，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| 提供商 | 短信服务商 | `twilio` |
| Account SID | Twilio Account SID | `ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| Auth Token | Twilio Auth Token | `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| 发送号码 | Twilio 电话号码 | `+1234567890` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  sms:
    provider: twilio
    account_sid: "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    auth_token: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    from_number: "+1234567890"
```

阿里云短信配置：

```yaml
channels:
  sms:
    provider: aliyun
    access_key_id: "LTAI5txxxxxxxxxx"
    access_key_secret: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    sign_name: "Echo"
    template_code: "SMS_123456789"
    region: "cn-hangzhou"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

向 Twilio 号码发送短信，如果收到 AI 回复短信则说明接入成功。

## 支持的功能

| 功能 | 支持状态 |
|---|---|
| 文本消息 | ✅ |
| 图片收发 | ❌ |
| 文件收发 | ❌ |
| 流式编辑（打字机效果）| ❌ |
| 输入指示器 | ❌ |
| 表情回应 | ❌ |

## Webhook 配置

Twilio 通过 Webhook 推送接收到的短信事件。在 Twilio Console 的电话号码配置中设置 Webhook URL：

Webhook URL 格式：`https://your-domain.com/api/channels/sms/webhook`

HTTP 方法选择 `POST`。

阿里云短信和腾讯云短信需通过回调 URL 接收短信上行（用户回复）：

```yaml
channels:
  sms:
    provider: aliyun
    callback_url: "https://your-domain.com/api/channels/sms/webhook"
```

## 常见问题

### Q: 国内短信发送失败怎么办？
A: 1) 确认签名和模板已审核通过；2) 确认发送内容符合模板格式；3) 检查手机号格式（需加国际区号）；4) 确认账户余额充足。

### Q: 短信有长度限制怎么办？
A: 标准短信限制 70 个中文字符或 160 个英文字符。Echo Agent 会自动将长回复拆分为多条短信发送。可在配置中设置 `max_segment_length` 控制每条短信的最大长度。

### Q: 如何避免短信费用过高？
A: 1) 在配置中设置 `rate_limit` 限制每用户每日发送条数；2) 设置 `max_segments` 限制单次回复最大短信条数；3) 使用 `cooldown_seconds` 设置同一用户的回复冷却时间。

## 相关链接

- [Twilio SMS API 文档](https://www.twilio.com/docs/sms)
- [阿里云短信服务文档](https://help.aliyun.com/product/44282.html)
- [腾讯云短信服务文档](https://cloud.tencent.com/document/product/382)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/sms)
