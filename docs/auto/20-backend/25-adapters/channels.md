# Adapters · Channels

> 外部 channel adapter (Slack / Discord / 微信 / …) · 必须走 validation safe_send 才允许出站。

**Source**: `runtime/adapters/channels/`

## Exports

- `Attachment`
- `BlueBubblesChannel`
- `BlueBubblesError`
- `Channel`
- `ChannelManager`
- `ChannelMetadata`
- `ChannelRoutingError`
- `DingTalkChannel`
- `DingTalkError`
- `DingTalkSignatureError`
- `DiscordChannel`
- `DiscordError`
- `DiscordSignatureError`
- `EmailChannel`
- `EmailError`
- `FeishuChannel`
- `FeishuError`
- `FeishuSignatureError`
- `GoogleChatChannel`
- `GoogleChatError`
- `GoogleChatSignatureError`
- `HomeAssistantChannel`
- `HomeAssistantError`
- `InboundMessage`
- `LineChannel`
- `LineError`
- `LineSignatureError`
- `MattermostChannel`
- `MattermostError`
- `MattermostSignatureError`
- `MatrixChannel`
- `MatrixError`
- `MatrixSignatureError`
- `NtfyChannel`
- `NtfyError`
- `OpenWebUIChannel`
- `OpenWebUIError`
- `OutboundMessage`
- `QQBotChannel`
- `QQBotError`
- `QQBotSignatureError`
- `QRLoginTimeout`
- `SignalChannel`
- `SignalError`
- `SignalSignatureError`
- `SimpleXChannel`
- `SimpleXError`
- `SlackChannel`
- `SlackSignatureError`
- `SmsChannel`
- `SmsError`
- `SmsSignatureError`
- `TeamsChannel`
- `TeamsError`
- `TeamsSignatureError`
- `TelegramChannel`
- `TelegramError`
- `TelegramSecretMismatch`
- `ThreadConversationStore`
- `WeComChannel`
- `WeComError`
- `WeComSignatureError`
- `WebhooksChannel`
- `WebhooksError`
- `WebhooksSignatureError`
- `WhatsAppChannel`
- `WhatsAppError`
- `WhatsAppSignatureError`
- `WeixinBotChannel`
- `WeixinBotError`
- `YuanbaoChannel`
- `YuanbaoError`
- `YuanbaoSignatureError`
- `resolve_attachment_data`

## Modules

| Module | Summary |
| --- | --- |
| `base.py` | — |
| `bluebubbles.py` | — |
| `dingtalk.py` | — |
| `discord.py` | — |
| `email.py` | — |
| `feishu.py` | — |
| `google_chat.py` | — |
| `homeassistant.py` | — |
| `line.py` | — |
| `manager.py` | — |
| `matrix.py` | — |
| `mattermost.py` | — |
| `ntfy.py` | — |
| `open_webui.py` | — |
| `qqbot.py` | — |
| `signal.py` | — |
| `simplex.py` | — |
| `slack.py` | — |
| `sms.py` | — |
| `store.py` | — |
| `teams.py` | — |
| `telegram.py` | — |
| `webhooks.py` | — |
| `wecom.py` | — |
| `weixin_bot.py` | — |
| `whatsapp.py` | — |
| `yuanbao.py` | — |

## Who imports this

**4** file(s) reference this package:

- **`runtime/cli_serve.py/`** · 1 file(s)
  - `runtime/cli_serve.py`
- **`runtime/execution/`** · 1 file(s)
  - `runtime/execution/suckers/cron_skills.py`
- **`runtime/sensing/`** · 2 file(s)
  - `runtime/sensing/gateway/_channels_constructors.py`
  - `runtime/sensing/gateway/channels_router.py`

