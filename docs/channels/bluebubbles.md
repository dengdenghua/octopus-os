# BlueBubbles 接入指南

## 概述

通过 BlueBubbles Server 将 Echo Agent 接入 iMessage，实现通过 Apple 生态系统与 AI 进行对话交互。

## 前置条件

- 一台运行 macOS 12.0 及以上的 Mac（需始终在线）
- 已配置 iMessage 账号
- 部署 BlueBubbles Server
- 公网可访问的服务器（用于接收 Webhook）

## 5 分钟快速接入

### 1. 获取凭证

1. 下载并安装 [BlueBubbles Server](https://bluebubbles.app/)
2. 启动 BlueBubbles Server，在设置向导中：
   - 配置 iMessage 账号
   - 设置服务器密码
   - 配置端口（默认 1234）
   - 启用 API 访问
3. 在「API」设置中生成 API 密钥
4. 记录服务器地址和 API 密钥

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 BlueBubbles，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| 服务器地址 | BlueBubbles Server URL | `http://192.168.1.100:1234` |
| API 密钥 | BlueBubbles API 密钥 | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| 轮询间隔 | 检查新消息的间隔（秒） | `5` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  bluebubbles:
    server_url: "http://192.168.1.100:1234"
    api_key: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    poll_interval: 5
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

向 Mac 上 iMessage 绑定的手机号/邮箱发送消息，如果收到 AI 回复则说明接入成功。

## 支持的功能

| 功能 | 支持状态 |
|---|---|
| 文本消息 | ✅ |
| 图片收发 | ✅ |
| 文件收发 | ✅ |
| 流式编辑（打字机效果）| ❌ |
| 输入指示器 | ✅ |
| 表情回应 | ✅ |

## Webhook 配置

BlueBubbles 支持 WebSocket 实时推送和 Webhook 回调两种方式接收新消息事件。

**WebSocket 模式（推荐）：**

Echo Agent 默认通过 WebSocket 连接 BlueBubbles Server 接收实时消息，无需额外配置。

**Webhook 模式：**

在 BlueBubbles Server 的「Settings」→「Webhooks」中添加 Webhook URL：

Webhook URL 格式：`https://your-domain.com/api/channels/bluebubbles/webhook`

```yaml
channels:
  bluebubbles:
    server_url: "http://192.168.1.100:1234"
    api_key: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    mode: webhook
    webhook_url: "https://your-domain.com/api/channels/bluebubbles/webhook"
```

## 常见问题

### Q: BlueBubbles Server 无法启动怎么办？
A: 1) 确认 macOS 版本为 12.0 及以上；2) 确认已登录 iMessage 账号；3) 检查端口是否被占用；4) 查看 BlueBubbles Server 日志排查错误。

### Q: 机器人无法发送消息怎么办？
A: 1) 确认 iMessage 账号发送功能正常（在 Mac 上手动发送测试）；2) 检查 API 密钥是否正确；3) 确认目标联系人已使用 iMessage（非 SMS）。

### Q: Mac 休眠后机器人无响应怎么办？
A: 1) 在「系统设置」→「节能」中关闭自动休眠；2) 使用 `caffeinate` 命令防止休眠：`caffeinate -d`；3) 考虑使用 Mac Mini 作为专用服务器。

## 相关链接

- [BlueBubbles 官方网站](https://bluebubbles.app/)
- [BlueBubbles Server GitHub](https://github.com/BlueBubblesApp/bluebubbles-server)
- [BlueBubbles API 文档](https://documenter.getpostman.com/view/11337661/UVFnfdFX)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/bluebubbles)
