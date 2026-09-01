# QQ 机器人接入指南

## 概述

通过 QQ 开放平台或 NapCat/LLOneBot 等第三方框架将 Echo Agent 接入 QQ，支持在私聊和群聊中与用户进行智能对话。

## 前置条件

- 一个 QQ 账号（建议使用小号作为机器人）
- 选择接入方式：QQ 开放平台官方机器人 或 NapCat/LLOneBot 第三方框架
- 如使用官方机器人：需在 QQ 开放平台注册开发者并创建应用

## 5 分钟快速接入

### 1. 获取凭证

**方式 A：QQ 开放平台（官方）**

1. 访问 [QQ 开放平台](https://q.qq.com/)，注册开发者账号
2. 创建机器人应用，获取 AppID 和 Token
3. 配置机器人信息和意图

**方式 B：NapCat（推荐，功能更丰富）**

1. 部署 NapCat：
   ```bash
   docker run -d --name napcat \
     -p 3000:3000 \
     -p 6099:6099 \
     mlikiowa/napcat-docker:latest
   ```
2. 通过 WebUI 扫码登录 QQ 账号
3. 在 NapCat 配置中开启 OneBot 11 HTTP 和 WebSocket 接口

**方式 C：LLOneBot**

1. 安装 LLOneBot 插件到 QQNT
2. 在 LLOneBot 设置中开启 HTTP 和 WebSocket 接口
3. 配置反向 WebSocket 地址

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 QQ 机器人，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| 接入方式 | official / napcat / llonebot | `napcat` |
| AppID | QQ 开放平台 AppID | `1020xxxxxxxx` |
| Token | QQ 开放平台 Token | `xxxxxxxxxxxxxxxx` |
| OneBot HTTP 地址 | NapCat/LLOneBot HTTP 接口 | `http://localhost:3000` |
| OneBot WS 地址 | NapCat/LLOneBot WebSocket 接口 | `ws://localhost:3001` |
| Access Token | OneBot 访问令牌 | `your-access-token` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  qqbot:
    provider: napcat
    onebot_http: "http://localhost:3000"
    onebot_ws: "ws://localhost:3001"
    access_token: "your-access-token"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在 QQ 中向机器人账号发送私聊消息或在群聊中 @机器人，如果收到 AI 回复则说明接入成功。

## 支持的功能

| 功能 | 支持状态 |
|---|---|
| 文本消息 | ✅ |
| 图片收发 | ✅ |
| 文件收发 | ✅ |
| 流式编辑（打字机效果）| ❌ |
| 输入指示器 | ❌ |
| 表情回应 | ✅ |

## Webhook 配置

**QQ 开放平台模式：**

在 QQ 开放平台的机器人配置中设置事件回调地址：

Webhook URL 格式：`https://your-domain.com/api/channels/qqbot/webhook`

**NapCat/LLOneBot 模式：**

使用反向 WebSocket 推送事件。在 NapCat/LLOneBot 配置中添加反向 WebSocket 地址：

```
ws://your-server:port/api/channels/qqbot/ws
```

Echo Agent 会自动处理 WebSocket 连接和消息分发。

## 常见问题

### Q: NapCat 扫码登录后频繁掉线怎么办？
A: 1) 确保不在手机端同时登录同一 QQ 号；2) 检查 NapCat 容器资源是否充足；3) 在 NapCat 配置中启用自动重连；4) 避免频繁发送消息触发风控。

### Q: QQ 开放平台机器人无法接收群消息怎么办？
A: QQ 开放平台对群消息有严格限制，需申请「群@机器人」意图权限。建议使用 NapCat 方式获取更完整的功能支持。

### Q: 如何避免 QQ 账号被封？
A: 1) 使用小号作为机器人；2) 控制消息发送频率；3) 避免发送敏感内容；4) 不要在过多群组中同时活跃；5) 使用官方机器人方式更安全但功能受限。

## 相关链接

- [QQ 开放平台](https://q.qq.com/)
- [NapCat GitHub](https://github.com/NapNeko/NapCatQQ)
- [LLOneBot GitHub](https://github.com/LLOneBot/LLOneBot)
- [OneBot 11 协议规范](https://github.com/botuniverse/onebot-11)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/qqbot)
