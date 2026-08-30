from __future__ import annotations

import json
import logging
import time
import urllib.parse
from datetime import datetime
from typing import Any

from .base import Attachment, Channel, InboundMessage, OutboundMessage, _sanitize_url

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )

    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:  # pragma: no cover
    CRYPTOGRAPHY_AVAILABLE = False
    Ed25519PublicKey = None  # type: ignore[assignment, misc]
    InvalidSignature = Exception  # type: ignore[assignment, misc]


logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://discord.com/api/v10"

# Interaction types（Discord docs）
INTERACTION_PING = 1
INTERACTION_APPLICATION_COMMAND = 2
INTERACTION_MESSAGE_COMPONENT = 3


class DiscordError(RuntimeError):
    pass


class DiscordSignatureError(ValueError):
    pass


class DiscordChannel(Channel):
    channel_id: str = "discord"
    supports_edit: bool = True
    supports_typing: bool = True
    supports_reactions: bool = True

    def __init__(
        self,
        *,
        bot_token: str,
        public_key: str,
        channel_id: str = "discord",
        api_base_url: str = DEFAULT_API_BASE,
        http_client: Any = None,
    ) -> None:
        if not bot_token:
            raise ValueError("bot_token required")
        if not public_key:
            raise ValueError("public_key required")
        if not CRYPTOGRAPHY_AVAILABLE:
            raise RuntimeError(
                "`cryptography` package required for Ed25519 verify · "
                "`pip install cryptography` (or extras '.[discord]')",
            )

        self._bot_token = bot_token
        self._public_key_hex = public_key.lower()
        try:
            key_bytes = bytes.fromhex(self._public_key_hex)
            self._verifier = Ed25519PublicKey.from_public_bytes(key_bytes)
        except (ValueError, Exception) as e:  # noqa: BLE001
            raise ValueError(f"invalid public_key hex: {e}") from e
        self.channel_id = channel_id
        self.api_base_url = api_base_url.rstrip("/")
        self._http = http_client
        self.send_log: list[OutboundMessage] = []

    # ─── Channel ABC ────────────────────────────

    def start(self) -> None:
        pass

    def stop(self) -> None:
        if hasattr(self, "_bare_client") and self._bare_client is not None:
            self._bare_client.close()
            self._bare_client = None

    def send(self, msg: OutboundMessage) -> None:
        self.send_log.append(msg)

        # Constitution gate · LINT-11 requires this before any network call.
        verdict = self.safe_send(msg)
        if verdict.action == "block":
            logger.warning(
                "channel.send.blocked",
                extra={"channel": self.channel_id, "reason": verdict.reason},
            )
            return
        # Use sanitized text if the gate rewrote PII · otherwise original.
        content = verdict.sanitized if verdict.action == "rewrite" else msg.content

        channel_id = msg.metadata.get("discord_channel_id") or msg.thread_id
        if not channel_id:
            raise DiscordError("missing discord channel_id for send")

        url = f"{self.api_base_url}/channels/{channel_id}/messages"

        if not msg.attachments:
            body: dict[str, Any] = {"content": content}
            reply_to = msg.metadata.get("message_reference_id")
            if reply_to:
                body["message_reference"] = {"message_id": str(reply_to)}

            resp_data = self._post_json(
                url,
                body=body,
                authorization=f"Bot {self._bot_token}",
            )
            if "id" not in resp_data and "content" not in resp_data:
                raise DiscordError(
                    f"unexpected send response shape: {str(resp_data)[:200]}",
                )
            return

        payload: dict[str, Any] = {"content": content}
        reply_to = msg.metadata.get("message_reference_id")
        if reply_to:
            payload["message_reference"] = {"message_id": str(reply_to)}

        files: list[tuple[str, tuple]] = []
        for i, att in enumerate(msg.attachments):
            file_data = att.data if isinstance(att.data, bytes) and att.data else b""
            if not file_data and att.url:
                if self._http is not None:
                    dl_resp = self._http.get(att.url, timeout=15.0)
                else:
                    if not hasattr(self, "_bare_client") or self._bare_client is None:
                        self._bare_client = httpx.Client(timeout=15.0)
                    dl_resp = self._bare_client.get(att.url)
                file_data = dl_resp.content
            files.append(
                (
                    f"files[{i}]",
                    (
                        att.filename or f"file_{i}",
                        file_data,
                        att.content_type or "application/octet-stream",
                    ),
                )
            )

        resp_data = self._post_multipart(
            url,
            payload=payload,
            files=files,
            authorization=f"Bot {self._bot_token}",
        )
        if "id" not in resp_data and "content" not in resp_data:
            raise DiscordError(
                f"unexpected send response shape: {str(resp_data)[:200]}",
            )

    def edit(self, msg: OutboundMessage, original_message_id: str) -> None:
        channel_id = msg.metadata.get("discord_channel_id") or msg.thread_id
        if not channel_id:
            raise DiscordError("missing discord channel_id for edit")

        url = f"{self.api_base_url}/channels/{channel_id}/messages/{original_message_id}"
        body: dict[str, Any] = {"content": msg.content}

        resp_data = self._patch_json(
            url,
            body=body,
            authorization=f"Bot {self._bot_token}",
        )
        if "id" not in resp_data and "content" not in resp_data:
            raise DiscordError(
                f"unexpected edit response shape: {str(resp_data)[:200]}",
            )

    def send_typing(self, thread_id: str) -> None:
        channel_id = thread_id
        url = f"{self.api_base_url}/channels/{channel_id}/typing"
        self._post_json(url, body={}, authorization=f"Bot {self._bot_token}")

    def add_reaction(self, thread_id: str, message_id: str, emoji: str) -> None:
        channel_id = thread_id
        encoded_emoji = urllib.parse.quote(emoji, safe="")
        url = f"{self.api_base_url}/channels/{channel_id}/messages/{message_id}/reactions/{encoded_emoji}/@me"
        self._put_json(url, body={}, authorization=f"Bot {self._bot_token}")

    def health_check(self) -> bool:
        try:
            url = f"{self.api_base_url}/users/@me"
            headers = {"Authorization": f"Bot {self._bot_token}"}
            if self._http is not None:
                client = self._http
            else:
                if not hasattr(self, "_bare_client") or self._bare_client is None:
                    self._bare_client = httpx.Client(timeout=15.0)
                client = self._bare_client
            resp = client.get(url, headers=headers)
            return resp.status_code == 200
        except Exception:
            return False

    # ─── HTTP ────────────────────────────────

    def _post_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
        authorization: str,
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": authorization,
        }
        if self._http is not None:
            client = self._http
        else:
            if not hasattr(self, "_bare_client") or self._bare_client is None:
                self._bare_client = httpx.Client(timeout=15.0)
            client = self._bare_client
        for attempt in range(4):
            try:
                resp = client.post(url, json=body, headers=headers)
            except Exception as e:  # noqa: BLE001
                raise DiscordError(
                    f"network: {type(e).__name__}: {e}",
                ) from e
            if resp.status_code < 500 and resp.status_code != 429:
                logger.info(
                    "channel.send", extra={"channel": self.channel_id, "status": resp.status_code}
                )
                break
            if attempt == 3:
                logger.error(
                    "channel.send.failed",
                    extra={"channel": self.channel_id, "status": resp.status_code},
                )
                break
            logger.warning(
                "channel.send.retry",
                extra={"channel": self.channel_id, "status": resp.status_code, "attempt": attempt},
            )
            time.sleep(2**attempt)
        status = getattr(resp, "status_code", 200)
        if status >= 400:
            raise DiscordError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise DiscordError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise DiscordError("response not an object")
        return data

    def _patch_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
        authorization: str,
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": authorization,
        }
        if self._http is not None:
            client = self._http
        else:
            if not hasattr(self, "_bare_client") or self._bare_client is None:
                self._bare_client = httpx.Client(timeout=15.0)
            client = self._bare_client
        for attempt in range(4):
            try:
                resp = client.patch(url, json=body, headers=headers)
            except Exception as e:  # noqa: BLE001
                raise DiscordError(
                    f"network: {type(e).__name__}: {e}",
                ) from e
            if resp.status_code < 500 and resp.status_code != 429:
                logger.info(
                    "channel.send", extra={"channel": self.channel_id, "status": resp.status_code}
                )
                break
            if attempt == 3:
                logger.error(
                    "channel.send.failed",
                    extra={"channel": self.channel_id, "status": resp.status_code},
                )
                break
            logger.warning(
                "channel.send.retry",
                extra={"channel": self.channel_id, "status": resp.status_code, "attempt": attempt},
            )
            time.sleep(2**attempt)
        status = getattr(resp, "status_code", 200)
        if status >= 400:
            raise DiscordError(
                f"PATCH {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise DiscordError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise DiscordError("response not an object")
        return data

    def _put_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
        authorization: str,
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": authorization,
        }
        if self._http is not None:
            client = self._http
        else:
            if not hasattr(self, "_bare_client") or self._bare_client is None:
                self._bare_client = httpx.Client(timeout=15.0)
            client = self._bare_client
        for attempt in range(4):
            try:
                resp = client.put(url, json=body, headers=headers)
            except Exception as e:  # noqa: BLE001
                raise DiscordError(
                    f"network: {type(e).__name__}: {e}",
                ) from e
            if resp.status_code < 500 and resp.status_code != 429:
                logger.info(
                    "channel.send", extra={"channel": self.channel_id, "status": resp.status_code}
                )
                break
            if attempt == 3:
                logger.error(
                    "channel.send.failed",
                    extra={"channel": self.channel_id, "status": resp.status_code},
                )
                break
            logger.warning(
                "channel.send.retry",
                extra={"channel": self.channel_id, "status": resp.status_code, "attempt": attempt},
            )
            time.sleep(2**attempt)
        status = getattr(resp, "status_code", 200)
        if status >= 400:
            raise DiscordError(
                f"PUT {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise DiscordError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise DiscordError("response not an object")
        return data

    def _post_multipart(
        self,
        url: str,
        *,
        payload: dict[str, Any],
        files: list[tuple[str, tuple]],
        authorization: str,
    ) -> dict[str, Any]:
        headers = {"Authorization": authorization}
        form_data = {"payload_json": json.dumps(payload)}
        if self._http is not None:
            client = self._http
        else:
            if not hasattr(self, "_bare_client") or self._bare_client is None:
                self._bare_client = httpx.Client(timeout=15.0)
            client = self._bare_client
        for attempt in range(4):
            try:
                resp = client.post(url, data=form_data, files=files, headers=headers)
            except Exception as e:  # noqa: BLE001
                raise DiscordError(
                    f"network: {type(e).__name__}: {e}",
                ) from e
            if resp.status_code < 500 and resp.status_code != 429:
                logger.info(
                    "channel.send", extra={"channel": self.channel_id, "status": resp.status_code}
                )
                break
            if attempt == 3:
                logger.error(
                    "channel.send.failed",
                    extra={"channel": self.channel_id, "status": resp.status_code},
                )
                break
            logger.warning(
                "channel.send.retry",
                extra={"channel": self.channel_id, "status": resp.status_code, "attempt": attempt},
            )
            time.sleep(2**attempt)
        status = getattr(resp, "status_code", 200)
        if status >= 400:
            raise DiscordError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            resp_data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise DiscordError(f"json parse: {e}") from e
        if not isinstance(resp_data, dict):
            raise DiscordError("response not an object")
        return resp_data

    def verify_signature(
        self,
        *,
        body: bytes,
        signature_hex: str,
        timestamp: str,
    ) -> None:
        if not signature_hex or not timestamp:
            raise DiscordSignatureError(
                "missing X-Signature-Ed25519 / Timestamp header",
            )
        try:
            sig_bytes = bytes.fromhex(signature_hex)
        except ValueError as e:
            raise DiscordSignatureError(
                f"bad signature hex: {e}",
            ) from e
        message = timestamp.encode("utf-8") + body
        try:
            self._verifier.verify(sig_bytes, message)
        except InvalidSignature as e:
            raise DiscordSignatureError("invalid signature") from e

    def handle_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | dict[str, Any] | None:
        lowered = {k.lower(): v for k, v in headers.items()}
        sig = lowered.get("x-signature-ed25519", "")
        ts = lowered.get("x-signature-timestamp", "")
        self.verify_signature(body=body, signature_hex=sig, timestamp=ts)

        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"bad json body: {e}") from e
        if not isinstance(payload, dict):
            raise ValueError("payload not an object")

        itype = payload.get("type")
        if itype == INTERACTION_PING:
            return {"type": 1}

        if itype != INTERACTION_APPLICATION_COMMAND:
            return None

        data = payload.get("data") or {}
        if not isinstance(data, dict):
            return None

        command_name = data.get("name", "")
        options = data.get("options") or []
        text_parts: list[str] = []
        if isinstance(command_name, str) and command_name:
            text_parts.append(f"/{command_name}")
        if isinstance(options, list):
            for opt in options:
                if isinstance(opt, dict):
                    val = opt.get("value")
                    if isinstance(val, str) and val:
                        text_parts.append(val)
        text = " ".join(text_parts).strip()
        if not text:
            return None

        user = payload.get("member", {}).get("user") or payload.get("user") or {}
        sender_id = str(user.get("id", "")) if isinstance(user, dict) else ""
        channel_id = payload.get("channel_id", "")
        guild_id = payload.get("guild_id", "")
        interaction_id = payload.get("id", "")
        message_id = (
            payload.get("message", {}).get("id", "")
            if isinstance(payload.get("message"), dict)
            else ""
        )

        if not isinstance(channel_id, str) or not channel_id:
            return None

        attachments: list[Attachment] = []
        msg_obj = payload.get("message") or {}
        if isinstance(msg_obj, dict):
            for att in msg_obj.get("attachments", []):
                if isinstance(att, dict):
                    attachments.append(
                        Attachment(
                            content_type=att.get("content_type", "application/octet-stream"),
                            url=att.get("url", ""),
                            filename=att.get("filename", ""),
                        )
                    )

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=channel_id,
            sender_id=sender_id,
            content=text,
            metadata={
                "platform": "discord",
                "discord_channel_id": channel_id,
                "guild_id": guild_id,
                "interaction_id": interaction_id,
                "command_name": command_name,
                "message_reference_id": message_id,
            },
            received_at=_parse_discord_ts(payload.get("timestamp")),
            attachments=attachments,
        )


def _parse_discord_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
