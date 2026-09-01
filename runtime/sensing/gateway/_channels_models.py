from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# ═══════════════════════════════════════════════════════════
# Platform metadata + shared validation/utility helpers.
#
# Split out of channels_router.py (pure structural refactor —
# no logic changes). Imported back by channels_router.py.
# ═══════════════════════════════════════════════════════════

_PLATFORM_META: dict[str, dict[str, str]] = {
    "wechat": {
        "cls_name": "WeChatChannel",
        "display_name": "微信",
        "description": "微信 ClawBot · 扫码对接个人号 / 客服号",
        "help_url": "https://docs.echo.ai/channels/wechat",
    },
    "dingtalk": {
        "cls_name": "DingTalkChannel",
        "display_name": "钉钉",
        "description": "钉钉机器人",
        "help_url": "https://docs.echo.ai/channels/dingtalk",
    },
    "feishu": {
        "cls_name": "FeishuChannel",
        "display_name": "飞书",
        "description": "飞书机器人",
        "help_url": "https://docs.echo.ai/channels/feishu",
    },
    "telegram": {
        "cls_name": "TelegramChannel",
        "display_name": "Telegram",
        "description": "Telegram Bot",
        "help_url": "https://docs.echo.ai/channels/telegram",
    },
    "slack": {
        "cls_name": "SlackChannel",
        "display_name": "Slack",
        "description": "Slack 应用 / bot",
        "help_url": "https://docs.echo.ai/channels/slack",
    },
    "discord": {
        "cls_name": "DiscordChannel",
        "display_name": "Discord",
        "description": "Discord Interaction Bot",
        "help_url": "https://docs.echo.ai/channels/discord",
    },
    "signal": {
        "cls_name": "SignalChannel",
        "display_name": "Signal",
        "description": "Signal Private Messenger",
        "help_url": "https://docs.echo.ai/channels/signal",
    },
    "whatsapp": {
        "cls_name": "WhatsAppChannel",
        "display_name": "WhatsApp",
        "description": "WhatsApp Business API",
        "help_url": "https://docs.echo.ai/channels/whatsapp",
    },
    "email": {
        "cls_name": "EmailChannel",
        "display_name": "Email",
        "description": "SMTP / IMAP 邮件通道",
        "help_url": "https://docs.echo.ai/channels/email",
    },
    "sms": {
        "cls_name": "SmsChannel",
        "display_name": "SMS (Twilio)",
        "description": "Twilio SMS 短信通道",
        "help_url": "https://docs.echo.ai/channels/sms",
    },
    "mattermost": {
        "cls_name": "MattermostChannel",
        "display_name": "Mattermost",
        "description": "Mattermost 开源协作平台",
        "help_url": "https://docs.echo.ai/channels/mattermost",
    },
    "matrix": {
        "cls_name": "MatrixChannel",
        "display_name": "Matrix",
        "description": "Matrix / Element 去中心化通信",
        "help_url": "https://docs.echo.ai/channels/matrix",
    },
    "wecom": {
        "cls_name": "WeComChannel",
        "display_name": "企业微信",
        "description": "WeCom 企业微信应用消息",
        "help_url": "https://docs.echo.ai/channels/wecom",
    },
    "qqbot": {
        "cls_name": "QQBotChannel",
        "display_name": "QQ 机器人",
        "description": "QQ 官方机器人",
        "help_url": "https://docs.echo.ai/channels/qqbot",
    },
    "teams": {
        "cls_name": "TeamsChannel",
        "display_name": "Microsoft Teams",
        "description": "Teams Bot Framework",
        "help_url": "https://docs.echo.ai/channels/teams",
    },
    "line": {
        "cls_name": "LineChannel",
        "display_name": "LINE",
        "description": "LINE Messaging API",
        "help_url": "https://docs.echo.ai/channels/line",
    },
    "homeassistant": {
        "cls_name": "HomeAssistantChannel",
        "display_name": "Home Assistant",
        "description": "智能家居控制中心",
        "help_url": "https://docs.echo.ai/channels/homeassistant",
    },
    "bluebubbles": {
        "cls_name": "BlueBubblesChannel",
        "display_name": "BlueBubbles (iMessage)",
        "description": "iMessage 桥接通道",
        "help_url": "https://docs.echo.ai/channels/bluebubbles",
    },
    "ntfy": {
        "cls_name": "NtfyChannel",
        "display_name": "ntfy",
        "description": "ntfy.sh 推送通知",
        "help_url": "https://docs.echo.ai/channels/ntfy",
    },
    "webhooks": {
        "cls_name": "WebhooksChannel",
        "display_name": "Webhooks",
        "description": "通用 Webhook 集成",
        "help_url": "https://docs.echo.ai/channels/webhooks",
    },
    "google_chat": {
        "cls_name": "GoogleChatChannel",
        "display_name": "Google Chat",
        "description": "Google Workspace 聊天机器人",
        "help_url": "https://docs.echo.ai/channels/google_chat",
    },
    "simplex": {
        "cls_name": "SimpleXChannel",
        "display_name": "SimpleX",
        "description": "SimpleX 隐私聊天",
        "help_url": "https://docs.echo.ai/channels/simplex",
    },
    "open_webui": {
        "cls_name": "OpenWebUIChannel",
        "display_name": "Open WebUI",
        "description": "Open WebUI 开源聊天界面",
        "help_url": "https://docs.echo.ai/channels/open_webui",
    },
    "yuanbao": {
        "cls_name": "YuanbaoChannel",
        "display_name": "腾讯元宝",
        "description": "腾讯元宝机器人",
        "help_url": "https://docs.echo.ai/channels/yuanbao",
    },
}

_FALLBACK_META = {
    "cls_name": "Channel",
    "display_name": "其他",
    "description": "自定义渠道",
    "help_url": "",
}

_FALLBACK_ASSIGNMENTS: dict[str, str] = {}

_WECHAT_QR_SESSIONS: dict[str, Any] = {}

_CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _is_oversized_file(path: Path, max_bytes: int) -> bool:
    try:
        return path.stat().st_size > max_bytes
    except OSError:
        return False


def _normalize_channel_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if _CHANNEL_ID_RE.fullmatch(text) else None


def _normalize_agent_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if _AGENT_ID_RE.fullmatch(text) else None


def _normalize_platform_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not _PLATFORM_RE.fullmatch(text) or text not in _PLATFORM_META:
        return None
    return text


def _normalize_pairing_ref(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 256 or "\x00" in text:
        return None
    return text


def _is_group_message(msg: Any) -> bool:
    meta = getattr(msg, "metadata", None) or {}
    if meta.get("is_group") is True:
        return True
    if meta.get("channel_type") in ("group", "channel", "supergroup"):
        return True
    thread_id = getattr(msg, "thread_id", "") or ""
    if thread_id.startswith(("C", "G")) and len(thread_id) > 3:  # noqa: SIM103 — explicit per-platform branches read better
        return True  # Slack C.../G...
    if thread_id.startswith("-100"):
        return True  # Telegram supergroup
    return thread_id.startswith("gid:")  # Google-style group id


def _guess_platform(channel_id: str, cls_name: str) -> str:
    hay = f"{channel_id} {cls_name}".lower()
    if "weixin" in hay:
        return "wechat"
    for platform in _PLATFORM_META:
        if platform in hay:
            return platform
    return "other"


def _zero_metrics() -> dict[str, int]:
    return {
        "pairings_count": 0,
        "group_count": 0,
        "pending_count": 0,
    }
