# Email 接入指南

## 概述

通过 IMAP/SMTP 协议将 Echo Agent 接入电子邮件，实现通过邮件与 AI 进行对话交互。

## 前置条件

- 一个邮箱账号（支持 IMAP 和 SMTP）
- 邮箱需开启 IMAP/SMTP 服务并获取授权码（如 Gmail 应用专用密码、QQ 邮箱授权码等）
- 服务器需能访问邮件服务器端口

## 5 分钟快速接入

### 1. 获取凭证

**Gmail：**

1. 开启两步验证：Google 账号 → 安全性 → 两步验证
2. 创建应用专用密码：Google 账号 → 安全性 → 应用专用密码
3. 记录生成的 16 位密码

**QQ 邮箱：**

1. 登录 QQ 邮箱 → 设置 → 账户
2. 开启 IMAP/SMTP 服务
3. 按提示获取授权码

**163 邮箱：**

1. 登录 163 邮箱 → 设置 → POP3/SMTP/IMAP
2. 开启 IMAP 服务
3. 按提示获取授权码

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 Email，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| IMAP 主机 | IMAP 服务器地址 | `imap.gmail.com` |
| IMAP 端口 | IMAP 服务器端口 | `993` |
| SMTP 主机 | SMTP 服务器地址 | `smtp.gmail.com` |
| SMTP 端口 | SMTP 服务器端口 | `587` |
| 用户名 | 邮箱地址 | `bot@example.com` |
| 密码 | 邮箱授权码/应用专用密码 | `xxxx xxxx xxxx xxxx` |
| 轮询间隔 | 检查新邮件的间隔（秒） | `30` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  email:
    imap_host: "imap.gmail.com"
    imap_port: 993
    smtp_host: "smtp.gmail.com"
    smtp_port: 587
    username: "bot@example.com"
    password: "xxxx xxxx xxxx xxxx"
    poll_interval: 30
    use_tls: true
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

向配置的邮箱地址发送一封邮件，等待轮询周期后检查是否收到 AI 回复邮件。

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

Email 渠道使用 IMAP 轮询方式接收邮件，无需配置 Webhook。Echo Agent 会定期检查收件箱中的新邮件并处理。

常见邮箱服务器配置：

| 邮箱 | IMAP 主机 | IMAP 端口 | SMTP 主机 | SMTP 端口 |
|---|---|---|---|---|
| Gmail | `imap.gmail.com` | 993 | `smtp.gmail.com` | 587 |
| Outlook | `outlook.office365.com` | 993 | `smtp.office365.com` | 587 |
| QQ 邮箱 | `imap.qq.com` | 993 | `smtp.qq.com` | 587 |
| 163 邮箱 | `imap.163.com` | 993 | `smtp.163.com` | 465 |
| 阿里企业邮箱 | `imap.qiye.aliyun.com` | 993 | `smtp.qiye.aliyun.com` | 465 |

## 常见问题

### Q: Gmail 登录失败怎么办？
A: Gmail 不支持使用账号密码直接登录，必须使用应用专用密码。确保已开启两步验证并创建了应用专用密码。

### Q: 邮件回复延迟较高怎么办？
A: 1) 减小 `poll_interval` 值（最小建议 10 秒）；2) 检查网络到邮件服务器的延迟；3) 考虑使用邮件转发 + Webhook 方式实现实时接收。

### Q: 如何避免重复回复同一封邮件？
A: Echo Agent 会自动记录已处理的邮件 Message-ID，确保不会重复处理。处理记录存储在 `~/.echo/data/email_processed.json` 中。

## 相关链接

- [Gmail IMAP 设置](https://support.google.com/mail/answer/7126229)
- [QQ 邮箱帮助中心](https://service.mail.qq.com/)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/email)
