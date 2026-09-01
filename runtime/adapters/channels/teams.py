from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

from .base import Channel, InboundMessage, OutboundMessage, _sanitize_url

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://login.microsoftonline.com"


class TeamsError(RuntimeError):
    pass


class TeamsSignatureError(ValueError):
    pass


class TeamsChannel(Channel):
    channel_id: str = "teams"

    def __init__(
        self,
        *,
        app_id: str,
        app_password: str,
        channel_id: str = "teams",
        api_base_url: str = DEFAULT_API_BASE,
        http_client: Any = None,
    ) -> None:
        if not app_id:
            raise ValueError("app_id required")
        if not app_password:
            raise ValueError("app_password required")
        if not HTTPX_AVAILABLE:
            raise RuntimeError(
                "`httpx` package required for TeamsChannel · "
                "`pip install httpx` (or extras '.[teams]')",
            )

        self._app_id = app_id
        self._app_password = app_password
        self.channel_id = channel_id
        self.api_base_url = api_base_url.rstrip("/")
        self._http = http_client
        self.send_log: list[OutboundMessage] = []
        self._token: str = ""
        self._token_expires: float = 0.0
        self._service_url: str = ""

    def _ensure_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expires - 60:
            return self._token
        url = f"{self.api_base_url}/botframework.com/oauth2/v2.0/token"
        body = {
            "grant_type": "client_credentials",
            "client_id": self._app_id,
            "client_secret": self._app_password,
            "scope": "https://api.botframework.com/.default",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if self._http is not None:
            resp = self._http.post(url, data=body, headers=headers)
        else:
            if not hasattr(self, "_bare_client") or self._bare_client is None:
                self._bare_client = httpx.Client(timeout=15.0)
            resp = self._bare_client.post(url, data=body, headers=headers)
        status = getattr(resp, "status_code", 200)
        if status >= 400:
            raise TeamsError(
                f"token HTTP {status}: {getattr(resp, 'text', '')[:200]}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise TeamsError(f"token json parse: {e}") from e
        if not isinstance(data, dict):
            raise TeamsError("token response not an object")
        access_token = data.get("access_token", "")
        expires_in = data.get("expires_in", 0)
        if not access_token:
            raise TeamsError("token response missing access_token")
        self._token = access_token
        self._token_expires = now + float(expires_in)
        return self._token

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

        service_url = msg.metadata.get("teams_service_url") or self._service_url
        if not service_url:
            raise TeamsError("missing service_url for send")
        conversation_id = msg.metadata.get("teams_conversation_id")
        if not conversation_id:
            raise TeamsError("missing teams_conversation_id for send")
        reply_to_id = msg.metadata.get("teams_activity_id", "")
        service_url = service_url.rstrip("/")
        url = f"{service_url}/v3/conversations/{conversation_id}/activities/{reply_to_id}"
        body: dict[str, Any] = {"type": "message", "text": content}
        token = self._ensure_token()
        self._post_json(url, body=body, authorization=f"Bearer {token}")

    def health_check(self) -> bool:
        try:
            token = self._ensure_token()
            return bool(token)
        except Exception:
            return False

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
                raise TeamsError(
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
            raise TeamsError(
                f"POST {_sanitize_url(url)} failed: HTTP {status}",
            )
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise TeamsError(f"json parse: {e}") from e
        if not isinstance(data, dict):
            raise TeamsError("response not an object")
        return data

    def handle_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | dict[str, Any] | None:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"bad json body: {e}") from e
        if not isinstance(payload, dict):
            raise ValueError("payload not an object")

        activity_type = payload.get("type", "")
        if activity_type == "ping":
            return {"type": "ping"}

        if activity_type != "message":
            return None

        service_url = payload.get("serviceUrl", "")
        if not isinstance(service_url, str) or not service_url:
            return None

        self._service_url = service_url

        text = payload.get("text", "")
        if not isinstance(text, str) or not text.strip():
            return None

        from_obj = payload.get("from") or {}
        sender_id = str(from_obj.get("id", "")) if isinstance(from_obj, dict) else ""

        conversation_obj = payload.get("conversation") or {}
        conversation_id = (
            str(conversation_obj.get("id", "")) if isinstance(conversation_obj, dict) else ""
        )

        activity_id = str(payload.get("id", ""))
        timestamp = payload.get("timestamp", "")

        if not conversation_id:
            return None

        thread_id = f"{conversation_id}:{activity_id}" if activity_id else conversation_id

        return InboundMessage(
            channel_id=self.channel_id,
            thread_id=thread_id,
            sender_id=sender_id,
            content=text.strip(),
            metadata={
                "platform": "teams",
                "teams_service_url": service_url,
                "teams_conversation_id": conversation_id,
                "teams_activity_id": activity_id,
            },
            received_at=_parse_teams_ts(timestamp),
        )


def _parse_teams_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
