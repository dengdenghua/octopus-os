# Signal 接入指南

## 概述

通过 signal-cli 或 signald 桥接将 Echo Agent 接入 Signal，实现端到端加密环境下的智能对话。

## 前置条件

- 一个 Signal 账号（手机号）
- 安装 signal-cli 或 signald 作为 Signal 协议桥接
- 服务器需支持 Java 运行时（signal-cli）或 Docker（signald）

## 5 分钟快速接入

### 1. 获取凭证

1. 安装 signal-cli：
   ```bash
   wget https://github.com/AsamK/signal-cli/releases/download/v0.13.0/signal-cli-0.13.0.tar.gz
   tar xf signal-cli-0.13.0.tar.gz
   ```
2. 注册 Signal 账号（使用辅助号码）：
   ```bash
   signal-cli -u +8613800138000 register
   signal-cli -u +8613800138000 verify <验证码>
   ```
3. 获取设备 ID 和账号凭证

### 2. 配置 Echo Agent

在 Web UI 的「渠道」页面选择 Signal，填写以下字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| 手机号 | Signal 注册的手机号 | `+8613800138000` |
| 桥接模式 | signal-cli 或 signald | `signald` |
| signald 套接字路径 | signald Unix Socket 路径 | `/var/run/signald/signald.sock` |

或通过配置文件 `~/.echo/config.yaml`：

```yaml
channels:
  signal:
    phone_number: "+8613800138000"
    bridge: signald
    signald_socket: "/var/run/signald/signald.sock"
```

### 3. 启动服务

```bash
echo serve
```

### 4. 验证

在 Signal 中向机器人绑定的手机号发送消息，如果收到 AI 回复则说明接入成功。

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

Signal 不使用传统 Webhook，而是通过 signal-cli/signald 的本地接口接收消息。Echo Agent 通过监听 signald 的 WebSocket 或 signal-cli 的 JSON RPC 接口获取新消息事件。

如需远程部署，可使用 signal-cli 的 JSON RPC 模式：

```bash
signal-cli -u +8613800138000 daemon --socket /tmp/signal-cli.sock
```

然后在配置中指定：

```yaml
channels:
  signal:
    bridge: signal-cli
    socket_path: "/tmp/signal-cli.sock"
```

## 常见问题

### Q: 注册 Signal 账号时收不到验证码怎么办？
A: Signal 对虚拟号码限制较严，建议使用真实手机号注册。如果使用 VoIP 号码可能被拒绝。也可以尝试通过语音验证码方式接收。

### Q: signald 连接超时怎么排查？
A: 1) 确认 signald 服务正在运行：`systemctl status signald`；2) 检查 Socket 文件是否存在且有读写权限；3) 查看 signald 日志：`journalctl -u signald -f`。

### Q: 如何让机器人加入群组？
A: 在 Signal 中将机器人号码邀请到群组，机器人会自动接收群组消息。需确保 signal-cli 已启用群组支持。

## 相关链接

- [signal-cli GitHub](https://github.com/AsamK/signal-cli)
- [signald GitHub](https://gitlab.com/signald/signald)
- [Signal 官方网站](https://signal.org/)
- [Echo Agent 渠道配置文档](https://docs.echo-agent.dev/channels/signal)
