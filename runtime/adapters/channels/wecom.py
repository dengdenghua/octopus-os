from __future__ import annotations

import base64
import hashlib
import logging
import struct
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

from .base import Attachment, Channel, InboundMessage, OutboundMessage, _sanitize_url

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"
TOKEN_REFRESH_MARGIN_SECONDS = 120


class WeComError(RuntimeError):
    pass


class WeComSignatureError(ValueError):
    pass


class WeComChannel(Channel):
    channel_id: str = "wecom"
    supports_edit: bool = True

    def __init__(
        self,
        *,
        corp_id: str,
        agent_id: str,
        secret: str,
        token: str,
        encoding_aes_key: str,
        channel_id: str = "wecom",
        api_base_url: str = DEFAULT_API_BASE,
        http_client: Any = None,
    ) -> None:
        if not corp_id:
            raise ValueError("corp_id required")
        if not agent_id:
            raise ValueError("agent_id required")
        if not secret:
            raise ValueError("secret required")
        if not token:
            raise ValueError("token required")
        if not encoding_aes_key:
            raise ValueError("encoding_aes_key required")
        self._corp_id = corp_id
        self._agent_id = agent_id
        self._secret = secret
        self._token = token
        self._encoding_aes_key = encoding_aes_key
        self.channel_id = channel_id
        self.api_base_url = api_base_url.rstrip("/")
        self._http = http_client

        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0
        self._access_token_lock = threading.Lock()

        self.send_log: list[OutboundMessage] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        if hasattr(self, "_bare_client") and self._bare_client is not None:
            self._bare_client.close()
            self._bare_client = None

    def health_check(self) -> bool:
        try:
            url = f"{self.api_base_url}/gettoken?corpid={self._corp_id}&corpsecret={self._secret}"
            resp = self._post_json(url, body={}, bearer=None)
            return resp.get("errcode", -1) == 0
        except Exception:
            return False

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

        access_token = self._get_access_token()
        user = msg.metadata.get("touser") or msg.thread_id.split(":")[0] if msg.thread_id else ""
        if not user:
            raise WeComError("missing touser for send")

        url = f"{self.api_base_url}/message/send?access_token={access_token}"

        if msg.content:
            body = {
                "touser": user,
                "msgtype": "text",
                "agentid": int(self._agent_id),
                "text": {"content": content},
            }
            resp = self._post_json(url, body=body, bearer=None)
            errcode = resp.get("errcode", 0)
            if errcode != 0:
                raise WeComError(
                    f"wecom send failed: errcode={errcode} errmsg={resp.get('errmsg', '')[:200]}",
                )

        for att in msg.attachments:
            is_image = att.content_type.startswith("image/")
            upload_url = f"{self.api_base_url}/media/upload?access_token={access_token}&type={'image' if is_image else 'file'}"
            files = {
                "media": (
                    att.filename or ("image" if is_image else "file"),
                    att.data if isinstance(att.data, bytes) else att.data.encode(),
                    att.content_type,
                )
            }
            if self._http is not None:
                upload_resp = self._http.post(upload_url, files=files)
            else:
                if not hasattr(self, "_bare_client") or self._bare_client is None:
                    self._bare_client = httpx.Client(timeout=15.0)
                upload_resp = self._bare_client.post(upload_url, files=files, timeout=30.0)
            upload_data = upload_resp.json()
            media_id = upload_data.get("media_id", "")
            if not media_id:
                raise WeComError(
                    f"wecom media upload failed: {upload_data.get('errmsg', '')[:200]}"
                )

            if is_image:
                att_body = {
                    "touser": user,
                    "msgtype": "image",
                    "agentid": int(self._agent_id),
                    "image": {"media_id": media_id},
                }
            else:
                att_body = {
                    "touser": user,
                    "msgtype": "file",
                    "agentid": int(self._agent_id),
                    "file": {"media_id": media_id},
                }
            att_resp = self._post_json(url, body=att_body, bearer=None)
            att_errcode = att_resp.get("errcode", 0)
            if att_errcode != 0:
                raise WeComError(
                    f"wecom send attachment failed: errcode={att_errcode} "
                    f"errmsg={att_resp.get('errmsg', '')[:200]}",
                )

    def edit(self, msg: OutboundMessage, original_message_id: str) -> None:
        access_token = self._get_access_token()
        url = f"{self.api_base_url}/message/update?access_token={access_token}"
        body = {
            "message_id": original_message_id,
            "text": {"content": msg.content},
        }
        resp = self._post_json(url, body=body, bearer=None)
        errcode = resp.get("errcode", 0)
        if errcode != 0:
            raise WeComError(
                f"wecom edit failed: errcode={errcode} errmsg={resp.get('errmsg', '')[:200]}",
            )

    def _get_access_token(self) -> str:
        now = time.time()
        with self._access_token_lock:
            if (
                self._access_token
                and self._access_token_expires_at > now + TOKEN_REFRESH_MARGIN_SECONDS
            ):
                return self._access_token
            url = f"{self.api_base_url}/gettoken?corpid={self._corp_id}&corpsecret={self._secret}"
            resp = self._post_json(url, body={}, bearer=None)
            errcode = resp.get("errcode", 0)
            if errcode != 0:
                raise WeComError(
                    f"access_token refresh failed: "
                    f"errcode={errcode} errmsg={resp.get('errmsg', '')}",
                )
            access_token = resp.get("access_token")
            expires_in = int(resp.get("expires_in", 7200))
            if not isinstance(access_token, str) or not access_token:
                raise WeComError("no access_token in response")
            self._access_token = access_token
            self._access_token_expires_at = now + expires_in
            return access_token

    def _post_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
        bearer: str | None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"

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
                raise WeComError(
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
            raise WeComError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise WeComError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise WeComError("response not an object")
        return data

    def _verify_signature(
        self,
        *,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        encrypt: str,
    ) -> None:
        sort_list = sorted([self._token, timestamp, nonce, encrypt])
        # WeCom's callback protocol mandates SHA1 for this signature.
        sha1 = hashlib.sha1("".join(sort_list).encode("utf-8")).hexdigest()  # nosec B324
        if sha1 != msg_signature:
            raise WeComSignatureError("msg_signature verification failed")

    def _decrypt_message(self, encrypt: str) -> str:
        aes_key = base64.b64decode(self._encoding_aes_key + "=")
        ciphertext = base64.b64decode(encrypt)
        # AES-256-CBC per the WeCom callback spec, via the maintained
        # `cryptography` package (the old `Crypto` import was pyCrypto's
        # namespace and the package was never even declared or installed).
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        decryptor = Cipher(
            algorithms.AES(aes_key),
            modes.CBC(aes_key[:16]),
        ).decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        pad_len = plaintext[-1]
        plaintext = plaintext[:-pad_len]
        xml_len = struct.unpack("!I", plaintext[16:20])[0]
        xml_content = plaintext[20 : 20 + xml_len].decode("utf-8")
        from_corp_id = plaintext[20 + xml_len :].decode("utf-8")
        if from_corp_id != self._corp_id:
            raise WeComSignatureError("corp_id mismatch in decrypted message")
        return xml_content

    def handle_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | dict[str, Any] | None:
        try:
            xml_str = body.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ValueError(f"bad body encoding: {e}") from e

        root = ET.fromstring(xml_str)  # nosec B314 — py3.7+ ET has entity defenses; trusted WeChat callback

        msg_signature = root.findtext("MsgSignature", "")
        timestamp = root.findtext("TimeStamp", "")
        nonce = root.findtext("Nonce", "")
        encrypt = root.findtext("Encrypt", "")

        if not encrypt:
            return None

        self._verify_signature(
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypt=encrypt,
        )

        decrypted_xml = self._decrypt_message(encrypt)
        msg_root = ET.fromstring(decrypted_xml)  # nosec B314 — decrypted from trusted WeChat payload

        content = msg_root.findtext("Content", "")
        from_user = msg_root.findtext("FromUserName", "")
        msg_id = msg_root.findtext("MsgId", "")
        create_time = msg_root.findtext("CreateTime", "")
        msg_type = msg_root.findtext("MsgType", "text")

        attachments: list[Attachment] = []
        if msg_type == "image":
            pic_url = msg_root.findtext("PicUrl", "")
            media_id = msg_root.findtext("MediaId", "")
            if pic_url or media_id:
                attachments.append(
                    Attachment(
                        content_type="image",
                        url=pic_url,
                        metadata={"MediaId": media_id},
                    )
                )
        elif msg_type == "file":
            file_url = msg_root.findtext("FileUrl", "")
            media_id = msg_root.findtext("MediaId", "")
            file_name = msg_root.findtext("FileName", "")
            if file_url or media_id:
                attachments.append(
                    Attachment(
                        content_type="application/octet-stream",
                        url=file_url,
                        filename=file_name,
                        metadata={"MediaId": media_id},
                    )
                )

        if (not content or not content.strip()) and not attachments:
            return None

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=f"{from_user}:{msg_id}",
            sender_id=from_user,
            content=content.strip(),
            metadata={
                "platform": "wecom",
                "msg_id": msg_id,
                "from_user": from_user,
                "agent_id": self._agent_id,
                "touser": from_user,
            },
            received_at=_parse_wecom_ts(create_time),
            attachments=attachments,
        )


def _parse_wecom_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, (int, str)):
        return None
    try:
        ts = int(raw)
        return datetime.fromtimestamp(ts)
    except (ValueError, OverflowError, OSError):
        return None
