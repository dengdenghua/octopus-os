# Home Assistant 接入指南

## 概述

通过 Home Assistant 的 Assist Pipeline API 将 Echo Agent 接入智能家居平台，实现语音和文本的智能对话与家居控制。

## 前置条件

- 一个运行中的 Home Assistant 实例（版本 2023.1 及以上）
- 已安装 Home Assistant Cloud 或配置了 Nabu Casa（用于远程访问）
- 拥有 Home Assistant 管理员权限
- 已启用 Assist Pipeline 功能

## 5 分钟快速接入

### 1. 获取凭证

1. 登录 Home Assistant 管理界面
2. 在「设置」→「设备与服务」中创建长期访问令牌：
   - 点击左下角用户头像 → 「安全」选项卡 → 「长期访问令牌」
   - 点击「创建令牌」，输入名称（如 `echo-agent`），复制令牌
3. 在「设置」→「语音助手」中确认 Assist Pipeline 已启用
4. 记录 Home Assistant 的访问地址

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 Home Assistant，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| HA URL | Home Assistant 访问地址 | `http://homeassistant.local:8123` |
| 长期访问令牌 | HA 长期访问令牌 | `eyJ0eXAiOiJKV1Qi...` |
| Assist Pipeline | 语音助手管道 ID（可选） | `01HXXXXXXXXXXXXX` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  homeassistant:
    url: "http://homeassistant.local:8123"
    token: "eyJ0eXAiOiJKV1Qi..."
    pipeline_id: "01HXXXXXXXXXXXXX"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

通过 Echo Agent 向 Home Assistant 发送测试指令（如「打开客厅灯」），检查设备是否响应。或在 Home Assistant 的 Assist 界面中测试对话。

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

Home Assistant 支持 Webhook 自动化触发器。可在 HA 的自动化配置中创建 Webhook 触发器，将事件推送到 Echo Agent：

Webhook URL 格式：`https://your-domain.com/api/channels/homeassistant/webhook`

在 Home Assistant 的 `automations.yaml` 中配置：

```yaml
- alias: "转发 HA 通知到 Echo Agent"
  trigger:
    - platform: webhook
      webhook_id: "echo-notify"
      allowed_methods:
        - POST
  action:
    - service: rest_command.echo_notify
      data:
        message: "{{ trigger.json.message }}"
```

反向集成（Echo Agent → Home Assistant）通过 WebSocket API 实现：

```yaml
channels:
  homeassistant:
    url: "http://homeassistant.local:8123"
    token: "eyJ0eXAiOiJKV1Qi..."
    websocket_enabled: true
```

## 常见问题

### Q: 无法连接 Home Assistant 怎么办？
A: 1) 确认 HA 实例正在运行且 URL 可访问；2) 检查长期访问令牌是否有效；3) 如使用 HTTPS，确认 SSL 证书有效；4) 检查防火墙是否放行 8123 端口。

### Q: 如何让 AI 控制智能家居设备？
A: Echo Agent 会自动将 AI 的家居控制意图转换为 Home Assistant 服务调用。确保 HA 中已正确配置设备实体，并在 Echo Agent 的工具配置中启用 `homeassistant_control` 工具。

### Q: Assist Pipeline 响应慢怎么办？
A: 1) 检查 HA 服务器资源使用情况；2) 确认使用的 STT/TTS 引擎响应正常；3) 考虑使用本地 STT/TTS 引擎（如 Whisper + Piper）减少网络延迟。

## 相关链接

- [Home Assistant 官方网站](https://www.home-assistant.io/)
- [Home Assistant API 文档](https://developers.home-assistant.io/docs/api/rest)
- [Assist Pipeline 文档](https://www.home-assistant.io/voice_control/)
- [Home Assistant WebSocket API](https://developers.home-assistant.io/docs/api/websocket)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/homeassistant)
