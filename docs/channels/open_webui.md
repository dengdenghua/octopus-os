# Open WebUI 接入指南

## 概述

通过 Open WebUI 的 API 将 Echo Agent 接入 Open WebUI 界面，实现通过 Web 界面与 AI 进行对话交互。

## 前置条件

- 一个运行中的 Open WebUI 实例（版本 0.3.0 及以上）
- Open WebUI 管理员账号
- 了解 OpenAI 兼容 API 格式

## 5 分钟快速接入

### 1. 获取凭证

1. 登录 Open WebUI 管理界面
2. 在「设置」→「账户」中创建 API 密钥（或使用管理员令牌）
3. 记录 Open WebUI 的访问地址和 API 密钥

部署 Open WebUI（如尚未部署）：

```bash
docker run -d --name open-webui \
  -p 3000:8080 \
  -v open-webui:/app/backend/data \
  -e OPENAI_API_BASE_URL=http://echo-agent:8000/v1 \
  ghcr.io/open-webui/open-webui:main
```

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 Open WebUI，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| Open WebUI URL | Open WebUI 实例地址 | `http://localhost:3000` |
| API 密钥 | Open WebUI API 密钥 | `sk-xxxxxxxxxxxxxxxx` |
| 模型名称 | 在 Open WebUI 中显示的模型名 | `echo-agent` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  open_webui:
    url: "http://localhost:3000"
    api_key: "sk-xxxxxxxxxxxxxxxx"
    model_name: "echo-agent"
```

### 2. 配置 Open WebUI

在 Open WebUI 的「设置」→「连接」中配置 OpenAI API：

| 字段 | 值 |
|---|---|
| OpenAI API URL | `http://echo-agent-host:8000/v1` |
| API Key | Echo Agent 的 API 密钥 |

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在 Open WebUI 中选择 Echo Agent 模型，发送消息，如果收到 AI 回复则说明接入成功。

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

Open WebUI 通过 OpenAI 兼容 API（`/v1/chat/completions`）与 Echo Agent 通信，无需额外配置 Webhook。

Echo Agent 暴露的 API 端点：

| 端点 | 说明 |
|---|---|
| `GET /v1/models` | 列出可用模型 |
| `POST /v1/chat/completions` | 聊天补全（支持流式） |
| `POST /v1/images/generations` | 图片生成 |

Open WebUI 会自动调用这些端点完成对话交互。

## 常见问题

### Q: Open WebUI 中看不到 Echo Agent 模型怎么办？
A: 1) 确认 OpenAI API URL 配置正确且可访问；2) 检查 Echo Agent 是否正在运行；3) 在 Open WebUI 中点击「刷新模型列表」；4) 确认 API Key 正确。

### Q: 流式输出不生效怎么办？
A: 1) 确认 Open WebUI 设置中启用了流式输出；2) 检查 Echo Agent 的 `/v1/chat/completions` 端点是否返回 `stream: true` 格式的 SSE 响应；3) 确认网络代理未缓冲 SSE 响应。

### Q: 如何在 Open WebUI 中使用 Echo Agent 的工具功能？
A: Echo Agent 会将工具调用结果整合到 AI 回复中。Open WebUI 端无需额外配置，工具调用在 Echo Agent 内部自动完成。

## 相关链接

- [Open WebUI 官方网站](https://openwebui.com/)
- [Open WebUI GitHub](https://github.com/open-webui/open-webui)
- [Open WebUI 文档](https://docs.openwebui.com/)
- [OpenAI API 兼容格式](https://platform.openai.com/docs/api-reference/chat)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/open_webui)
