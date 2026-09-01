# Simplex 接入指南

## 概述

通过 SimpleX Chat 的 SMP 协议和 XFTP 将 Echo Agent 接入 SimpleX，实现完全去中心化、无用户标识的隐私智能对话。

## 剽置条件

- 安装 SimpleX CLI 或部署 simplex-chat headless 模式
- 了解 SimpleX 的连接地址（Connection Address）机制
- 服务器需能访问 SimpleX 服务器（SMP Server）

## 5 分钟快速接入

### 1. 获取凭证

1. 安装 SimpleX CLI：
   ```bash
   # macOS
   brew install simplex-chat

   # Linux
   curl -L https://github.com/simplex-chat/simplex-chat/releases/latest/download/simplex-chat-ubuntu-x86-64.tar.gz | tar xz
   ```
2. 启动 headless 模式并创建机器人配置：
   ```bash
   simplex-chat -p your-passphrase --port 5225
   ```
3. 获取机器人的 Connection Address（用于用户连接）
4. 记录 API 端口和密码

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 Simplex，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| API 地址 | SimpleX Chat Agent API 地址 | `http://localhost:5225` |
| API 密钥 | Agent API 认证密钥 | `your-api-key` |
| 自动接受联系 | 是否自动接受联系人请求 | `true` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  simplex:
    api_url: "http://localhost:5225"
    api_key: "your-api-key"
    auto_accept_contacts: true
    auto_join_groups: true
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

使用 SimpleX 客户端扫描机器人的 Connection Address 二维码或输入地址发起连接，发送消息后如果收到 AI 回复则说明接入成功。

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

SimpleX Chat 使用 Agent Client API（基于 WebSocket）接收事件，无需配置传统 Webhook。Echo Agent 启动后会通过 WebSocket 连接 SimpleX Chat Agent API 并监听新消息事件。

如需使用自定义 SMP 服务器，可在 SimpleX Chat 配置中指定：

```yaml
channels:
  simplex:
    api_url: "http://localhost:5225"
    smp_servers:
      - "smp://xxxxxxxx@smp1.simplex.im"
      - "smp://xxxxxxxx@smp2.simplex.im"
```

## 常见问题

### Q: 无法连接 SimpleX Chat Agent API 怎么办？
A: 1) 确认 simplex-chat 进程正在运行且端口正确；2) 检查 API 密钥是否匹配；3) 确认 WebSocket 连接未被防火墙阻断；4) 查看 simplex-chat 日志排查错误。

### Q: 用户连接机器人时需要做什么？
A: 用户需要获取机器人的 Connection Address（通过二维码或链接），在 SimpleX 客户端中添加联系人。如果开启了 `auto_accept_contacts`，机器人会自动接受连接请求。

### Q: SimpleX 的隐私优势是什么？
A: SimpleX 不使用用户标识（无用户 ID、无手机号），所有通信通过一次性地址路由，服务器无法知道谁在和谁通信。Echo Agent 不会存储任何用户身份信息。

## 相关链接

- [SimpleX Chat 官方网站](https://simplex.chat/)
- [SimpleX Chat GitHub](https://github.com/simplex-chat/simplex-chat)
- [SimpleX 协议文档](https://github.com/simplex-chat/simplex-chat/blob/stable/protocol.md)
- [SimpleX Agent Client API](https://github.com/simplex-chat/simplex-chat/blob/stable/docs/AGENT.md)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/simplex)
