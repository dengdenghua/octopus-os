from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ._channels_models import (
    _is_oversized_file,
    _normalize_channel_id,
    _normalize_platform_id,
)
from ._channels_persist import (
    _MAX_CREDENTIALS_FILE_BYTES,
    _clean_credentials_map,
    _sanitize_credentials_body,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# Channel constructor registry + credentials handling.
#
# Split out of channels_router.py (pure structural refactor —
# no logic changes). Imported back by channels_router.py.
# ═══════════════════════════════════════════════════════════


class _UnsupportedPlatformError(RuntimeError):
    pass


_CHANNEL_CONSTRUCTORS: dict[str, Callable[[dict[str, Any]], Any]] = {}


def register_channel_constructor(
    platform: str, constructor: Callable[[dict[str, Any]], Any]
) -> None:
    _CHANNEL_CONSTRUCTORS[platform] = constructor


def _credentials_on(manager: Any) -> dict[str, dict[str, Any]]:
    c = getattr(manager, "_channel_credentials", None)
    if c is None:
        c = {}
        with contextlib.suppress(AttributeError):
            manager._channel_credentials = c
    return c


def _mask(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    if len(value) <= 4:
        return "●" * len(value)
    return "●" * (len(value) - 4) + value[-4:]


_SENSITIVE_KEYS = {
    "bot_token",
    "signing_secret",
    "webhook_secret",
    "app_secret",
    "api_key",
    "api_secret",
    "token",
    "secret",
    "password",
    "encoding_aes_key",
    "corp_secret",
    "access_token",
}


def _mask_credentials(body: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in body.items():
        if k.lower() in _SENSITIVE_KEYS:
            out[k] = _mask(v)
        else:
            out[k] = v
    return out


def _require(body: dict[str, Any], key: str) -> str:
    v = body.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{key} required (non-empty string)")
    return v.strip()


def _optional(body: dict[str, Any], key: str) -> str | None:
    v = body.get(key)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def _construct_channel(platform: str, body: dict[str, Any]) -> Any:
    constructor = _CHANNEL_CONSTRUCTORS.get(platform)
    if constructor is None:
        raise _UnsupportedPlatformError(platform)
    return constructor(body)


def _make_slack(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels import SlackChannel

    return SlackChannel(
        bot_token=_require(body, "bot_token"),
        signing_secret=_require(body, "signing_secret"),
        channel_id=str(body.get("channel_id", "slack")),
    )


def _make_dingtalk(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.dingtalk import DingTalkChannel

    return DingTalkChannel(
        webhook_url=_require(body, "webhook_url"),
        secret=_optional(body, "secret"),
        channel_id=str(body.get("channel_id", "dingtalk")),
    )


def _make_feishu(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.feishu import FeishuChannel

    return FeishuChannel(
        app_id=_require(body, "app_id"),
        app_secret=_require(body, "app_secret"),
        verification_token=_require(body, "verification_token"),
        channel_id=str(body.get("channel_id", "feishu")),
    )


def _make_telegram(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.telegram import TelegramChannel

    return TelegramChannel(
        bot_token=_require(body, "bot_token"),
        webhook_secret=_optional(body, "webhook_secret") or "",
        channel_id=str(body.get("channel_id", "telegram")),
    )


def _make_discord(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.discord import DiscordChannel

    return DiscordChannel(
        bot_token=_require(body, "bot_token"),
        public_key=_require(body, "public_key"),
        channel_id=str(body.get("channel_id", "discord")),
    )


def _make_wechat(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.weixin_bot import WeixinBotChannel

    return WeixinBotChannel(
        bot_token=_require(body, "bot_token"),
        channel_id=str(body.get("channel_id", "weixin_bot")),
    )


def _make_signal(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.signal import SignalChannel

    return SignalChannel(
        phone_number=_require(body, "phone_number"),
        api_base_url=_optional(body, "api_base_url") or "http://localhost:8080",
        channel_id=str(body.get("channel_id", "signal")),
    )


def _make_whatsapp(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.whatsapp import WhatsAppChannel

    return WhatsAppChannel(
        phone_number_id=_require(body, "phone_number_id"),
        access_token=_require(body, "access_token"),
        verify_token=_require(body, "verify_token"),
        app_secret=_optional(body, "app_secret") or "",
        channel_id=str(body.get("channel_id", "whatsapp")),
    )


def _make_email(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.email import EmailChannel

    return EmailChannel(
        smtp_host=_require(body, "smtp_host"),
        smtp_port=int(body.get("smtp_port", 587)),
        imap_host=_require(body, "imap_host"),
        username=_require(body, "username"),
        password=_require(body, "password"),
        from_address=_require(body, "from_address"),
        channel_id=str(body.get("channel_id", "email")),
    )


def _make_sms(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.sms import SmsChannel

    return SmsChannel(
        account_sid=_require(body, "account_sid"),
        auth_token=_require(body, "auth_token"),
        from_number=_require(body, "from_number"),
        channel_id=str(body.get("channel_id", "sms")),
    )


def _make_mattermost(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.mattermost import MattermostChannel

    return MattermostChannel(
        bot_token=_require(body, "bot_token"),
        server_url=_require(body, "server_url"),
        channel_id=str(body.get("channel_id", "mattermost")),
    )


def _make_matrix(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.matrix import MatrixChannel

    return MatrixChannel(
        homeserver_url=_require(body, "homeserver_url"),
        access_token=_require(body, "access_token"),
        channel_id=str(body.get("channel_id", "matrix")),
    )


def _make_wecom(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.wecom import WeComChannel

    return WeComChannel(
        corp_id=_require(body, "corp_id"),
        agent_id=_require(body, "agent_id"),
        secret=_require(body, "secret"),
        token=_require(body, "token"),
        encoding_aes_key=_require(body, "encoding_aes_key"),
        channel_id=str(body.get("channel_id", "wecom")),
    )


def _make_qqbot(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.qqbot import QQBotChannel

    return QQBotChannel(
        app_id=_require(body, "app_id"),
        app_secret=_require(body, "app_secret"),
        channel_id_param=str(body.get("channel_id", "qqbot")),
    )


def _make_teams(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.teams import TeamsChannel

    return TeamsChannel(
        app_id=_require(body, "app_id"),
        app_password=_require(body, "app_password"),
        channel_id=str(body.get("channel_id", "teams")),
    )


def _make_line(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.line import LineChannel

    return LineChannel(
        channel_access_token=_require(body, "channel_access_token"),
        channel_secret=_require(body, "channel_secret"),
        channel_id=str(body.get("channel_id", "line")),
    )


def _make_homeassistant(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.homeassistant import HomeAssistantChannel

    return HomeAssistantChannel(
        ha_url=_require(body, "ha_url"),
        long_lived_token=_require(body, "long_lived_token"),
        channel_id=str(body.get("channel_id", "homeassistant")),
    )


def _make_bluebubbles(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.bluebubbles import BlueBubblesChannel

    return BlueBubblesChannel(
        server_url=_require(body, "server_url"),
        api_key=_require(body, "api_key"),
        password=_optional(body, "password") or "",
        channel_id=str(body.get("channel_id", "bluebubbles")),
    )


def _make_ntfy(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.ntfy import NtfyChannel

    return NtfyChannel(
        server_url=_optional(body, "server_url") or "https://ntfy.sh",
        topic=_require(body, "topic"),
        channel_id=str(body.get("channel_id", "ntfy")),
    )


def _make_webhooks(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.webhooks import WebhooksChannel

    return WebhooksChannel(
        webhook_secret=_require(body, "webhook_secret"),
        outbound_url=_require(body, "outbound_url"),
        channel_id=str(body.get("channel_id", "webhooks")),
    )


def _make_google_chat(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.google_chat import GoogleChatChannel

    return GoogleChatChannel(
        service_account_key=_require(body, "service_account_key"),
        channel_id=str(body.get("channel_id", "google_chat")),
    )


def _make_simplex(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.simplex import SimpleXChannel

    return SimpleXChannel(
        api_base_url=_optional(body, "api_base_url") or "http://localhost:5225",
        channel_id=str(body.get("channel_id", "simplex")),
    )


def _make_open_webui(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.open_webui import OpenWebUIChannel

    return OpenWebUIChannel(
        base_url=_require(body, "base_url"),
        api_key=_require(body, "api_key"),
        channel_id=str(body.get("channel_id", "open_webui")),
    )


def _make_yuanbao(body: dict[str, Any]) -> Any:
    from runtime.adapters.channels.yuanbao import YuanbaoChannel

    return YuanbaoChannel(
        bot_id=_require(body, "bot_id"),
        bot_token=_require(body, "bot_token"),
        channel_id=str(body.get("channel_id", "yuanbao")),
    )


register_channel_constructor("slack", _make_slack)
register_channel_constructor("dingtalk", _make_dingtalk)
register_channel_constructor("feishu", _make_feishu)
register_channel_constructor("telegram", _make_telegram)
register_channel_constructor("discord", _make_discord)
register_channel_constructor("wechat", _make_wechat)
register_channel_constructor("signal", _make_signal)
register_channel_constructor("whatsapp", _make_whatsapp)
register_channel_constructor("email", _make_email)
register_channel_constructor("sms", _make_sms)
register_channel_constructor("mattermost", _make_mattermost)
register_channel_constructor("matrix", _make_matrix)
register_channel_constructor("wecom", _make_wecom)
register_channel_constructor("qqbot", _make_qqbot)
register_channel_constructor("teams", _make_teams)
register_channel_constructor("line", _make_line)
register_channel_constructor("homeassistant", _make_homeassistant)
register_channel_constructor("bluebubbles", _make_bluebubbles)
register_channel_constructor("ntfy", _make_ntfy)
register_channel_constructor("webhooks", _make_webhooks)
register_channel_constructor("google_chat", _make_google_chat)
register_channel_constructor("simplex", _make_simplex)
register_channel_constructor("open_webui", _make_open_webui)
register_channel_constructor("yuanbao", _make_yuanbao)


def _load_credentials_and_bootstrap(
    manager: Any,
    creds_file: Path | None,
) -> None:
    if creds_file is None or not creds_file.exists():
        return
    if _is_oversized_file(creds_file, _MAX_CREDENTIALS_FILE_BYTES):
        logger.warning(
            "channel credentials load skipped: file too large (> %s bytes)",
            _MAX_CREDENTIALS_FILE_BYTES,
        )
        return
    try:
        raw = creds_file.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(
            "channel credentials load failed (%s): %s",
            type(e).__name__,
            e,
        )
        return
    if not isinstance(data, dict):
        return
    if data.get("_enc") == "aes-gcm":
        decrypted = _try_decrypt_payload(data, creds_file.parent)
        if decrypted is None:
            logger.warning(
                "channel credentials encrypted but decryption failed · "
                "skipping (wrong key / missing cryptography package?)",
            )
            return
        data = decrypted
    target = _credentials_on(manager)
    for platform, body in data.items():
        safe_platform = _normalize_platform_id(platform)
        if safe_platform is None or not isinstance(body, dict):
            continue
        try:
            clean_body = _sanitize_credentials_body(body)
            channel = _construct_channel(safe_platform, clean_body)
        except _UnsupportedPlatformError:
            continue
        except (ValueError, TypeError, KeyError) as e:
            logger.warning(
                "channel credentials for %s invalid · skipping: %s",
                safe_platform,
                e,
            )
            continue
        safe_channel_id = _normalize_channel_id(
            getattr(channel, "channel_id", None),
        )
        if safe_channel_id is None:
            logger.warning(
                "channel credentials for %s invalid · unsafe channel_id",
                safe_platform,
            )
            continue
        target[safe_platform] = clean_body
        if manager.has(safe_channel_id):
            continue  # Implementation note.
        try:
            manager.register(channel)
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.warning(
                "re-register %s failed: %s",
                safe_platform,
                e,
            )


def _save_credentials(manager: Any, creds_file: Path | None) -> None:
    if creds_file is None:
        return
    try:
        data = _clean_credentials_map(_credentials_on(manager))
        creds_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = creds_file.with_suffix(creds_file.suffix + ".tmp")
        encrypted = _try_encrypt_payload(data, creds_file.parent)
        if encrypted is not None:
            tmp.write_text(
                json.dumps(encrypted, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        os.replace(tmp, creds_file)
        with contextlib.suppress(OSError):
            os.chmod(creds_file, 0o600)
    except OSError as e:
        logger.warning("channel credentials save failed: %s", e)


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
#
#
#
#   { "_enc": "aes-gcm", "nonce": base64, "ciphertext": base64 }
#


def _aead_key(creds_dir: Path) -> bytes | None:
    import base64

    env_key = os.environ.get("ECHO_CREDENTIAL_KEY")
    if env_key:
        try:
            raw = base64.b64decode(env_key)
            if len(raw) == 32:
                return raw
            logger.warning(
                "ECHO_CREDENTIAL_KEY invalid length %d (need 32 bytes after base64)",
                len(raw),
            )
            return None
        except (ValueError, TypeError):
            logger.warning("ECHO_CREDENTIAL_KEY not valid base64")
            return None

    key_file = creds_dir / ".credential_key"
    try:
        if key_file.exists():
            raw = base64.b64decode(key_file.read_text(encoding="utf-8").strip())
            if len(raw) == 32:
                return raw
            logger.warning(
                ".credential_key length invalid · regenerating",
            )
        import secrets as _secrets

        raw = _secrets.token_bytes(32)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(
            base64.b64encode(raw).decode("ascii"),
            encoding="utf-8",
        )
        with contextlib.suppress(OSError):
            os.chmod(key_file, 0o600)
        return raw
    except OSError as e:
        logger.warning("could not read/create .credential_key: %s", e)
        return None


def _try_encrypt_payload(
    data: dict[str, Any],
    creds_dir: Path,
) -> dict[str, Any] | None:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        return None
    key = _aead_key(creds_dir)
    if key is None:
        return None
    try:
        import base64
        import secrets as _secrets

        nonce = _secrets.token_bytes(12)  # Implementation note.
        plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")
        ct = AESGCM(key).encrypt(nonce, plaintext, None)
        return {
            "_enc": "aes-gcm",
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ct).decode("ascii"),
        }
    except (ValueError, TypeError, OSError) as e:
        logger.warning("credential encryption failed: %s", e)
        return None


def _try_decrypt_payload(
    envelope: dict[str, Any],
    creds_dir: Path,
) -> dict[str, Any] | None:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        return None
    key = _aead_key(creds_dir)
    if key is None:
        return None
    try:
        import base64

        nonce = base64.b64decode(envelope["nonce"])
        ct = base64.b64decode(envelope["ciphertext"])
        pt = AESGCM(key).decrypt(nonce, ct, None)
        obj = json.loads(pt.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception as e:  # noqa: BLE001 — cryptography.InvalidTag, KeyError, JSON errors all collapse to "decrypt failed"
        logger.warning("credential decryption failed: %s", e)
        return None
