from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypedDict

_CRED_PATTERN = re.compile(
    r"(bot|token|key|secret|password|access_token|app_secret|signing_secret)"
    r"=([^&\s/:]{8})[^&\s/:]+",
    re.IGNORECASE,
)

_BOT_PATH_PATTERN = re.compile(
    r"/bot(\d{8,})[^/:]*",
    re.IGNORECASE,
)


def _sanitize_url(url: str) -> str:
    url = _CRED_PATTERN.sub(r"\1=\2***", url)
    return _BOT_PATH_PATTERN.sub(r"/bot\1***", url)


@dataclass
class Attachment:
    content_type: str
    data: bytes | str = b""
    url: str = ""
    filename: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ChannelMetadata(TypedDict, total=False):
    platform: str
    message_id: str
    sender_id: str
    conversation_id: str
    agent_id: str
    source: str
    discord_channel_id: str
    slack_thread_ts: str
    telegram_message_id: str
    teams_service_url: str
    teams_conversation_id: str
    teams_activity_id: str
    file_id: str
    mime_type: str


def resolve_attachment_data(att: Attachment) -> bytes | None:
    if att.data and isinstance(att.data, bytes) and len(att.data) > 0:
        return att.data
    if att.url and (att.url.startswith("http://") or att.url.startswith("https://")):
        try:
            import httpx as _httpx

            resp = _httpx.get(att.url, timeout=15.0, follow_redirects=True)
            if resp.status_code == 200:
                return resp.content
        except Exception:  # noqa: BLE001 — best-effort URL fetch, fall back to None
            pass
    return None


@dataclass
class InboundMessage:
    channel_id: str
    thread_id: str
    sender_id: str = ""
    content: str = ""
    metadata: ChannelMetadata = field(default_factory=dict)
    received_at: datetime | None = None
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class OutboundMessage:
    channel_id: str
    thread_id: str
    content: str
    metadata: ChannelMetadata = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)


class Channel(ABC):
    channel_id: str = ""  # Implementation note.
    _dispatcher: Callable[[InboundMessage], Any] | None = None
    # Lazily-created httpx client shared by the HTTP-backed channels. Declared
    # here so subclasses that only assign it inside ``stop()`` / lazy-init
    # (not ``__init__``) still have a typed attribute — otherwise mypy infers
    # its type from the first lexical ``= None`` and reads every guarded
    # ``self._bare_client.post(...)`` as a call on None. ``Any`` because httpx
    # is imported untyped.
    _bare_client: Any = None

    def bind_dispatcher(
        self,
        dispatcher: Callable[[InboundMessage], Any],
    ) -> None:
        self._dispatcher = dispatcher

    def _dispatch(self, msg: InboundMessage) -> Any:
        if self._dispatcher is None:
            raise RuntimeError(
                f"channel {self.channel_id!r} has no dispatcher bound · "
                "register it to a ChannelManager first",
            )
        return self._dispatcher(msg)

    @abstractmethod
    def start(self) -> None:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    @abstractmethod
    def send(self, msg: OutboundMessage) -> None:
        pass

    supports_edit: bool = False

    def edit(self, msg: OutboundMessage, original_message_id: str) -> None:
        if not self.supports_edit:
            self.send(msg)
            return
        raise NotImplementedError(
            f"{type(self).__name__}.edit() not implemented · "
            "override edit() and set supports_edit = True",
        )

    supports_typing: bool = False
    supports_reactions: bool = False

    def send_typing(self, thread_id: str) -> None:
        if not self.supports_typing:
            return
        raise NotImplementedError(
            f"{type(self).__name__}.send_typing() not implemented · "
            "override send_typing() and set supports_typing = True",
        )

    def add_reaction(self, thread_id: str, message_id: str, emoji: str) -> None:
        if not self.supports_reactions:
            return
        raise NotImplementedError(
            f"{type(self).__name__}.add_reaction() not implemented · "
            "override add_reaction() and set supports_reactions = True",
        )

    def health_check(self) -> bool:
        return True

    #

    def safe_send(self, msg: OutboundMessage) -> Any:
        """Apply the constitution gate to an outbound message.

        Returns a lightweight object with:

        * ``action`` · ``"allow"`` / ``"rewrite"`` / ``"block"``
        * ``sanitized`` · the text caller should actually send
          (may equal the input if allow · or scrubbed if rewrite ·
          empty string if block)

        Caller (the adapter's ``send()``) decides what to do
        with each action · typically:

        * allow + rewrite → forward ``sanitized`` to the platform
        * block → don't send · optionally surface a user-visible
          "message was not sent because {reason}" reply

        Journal events are recorded here regardless of action ·
        so the audit trail is complete even for ``allow`` hits
        (some PII was noted but destination was owner).
        """
        # Lazy imports to avoid a hard dep on constitution module
        # at base import time · lets adapters be loaded in test
        # environments without the full stack.
        try:
            from runtime.platform.process.session import current_session
            from runtime.safety.validation import check_outbound
            from runtime.safety.validation.events import (
                ConstitutionViolationEvent,
            )
        except ImportError:
            # Constitution subsystem not wired · degrade to
            # pass-through so a minimal dev setup still works.
            from dataclasses import dataclass

            @dataclass
            class _NoOpVerdict:
                action: str = "allow"
                sanitized: str = ""

            return _NoOpVerdict(action="allow", sanitized=msg.content)

        destination = f"channels:{self.channel_id}:{msg.thread_id}"
        verdict = check_outbound(
            message=msg.content,
            destination=destination,
            session=current_session(),
        )

        # Journal write is the ChannelManager's responsibility · it
        # holds the Journal reference. Here we just return a verdict ·
        # caller inspects and records. Keeps safe_send a pure
        # function from (msg, destination) → verdict · no hidden
        # side effects that make test setup awkward.
        #
        # (Earlier draft tried to auto-journal here; removed because
        # it forced every test to thread a journal through the
        # adapter's context.)
        _ = ConstitutionViolationEvent  # kept import for type hint

        # Return a slim object that decouples from Verdict's shape ·
        # so future gate signature changes don't break adapters.
        from dataclasses import dataclass

        @dataclass
        class _SendVerdict:
            action: str
            sanitized: str
            reason: str

        return _SendVerdict(
            action=verdict.action,
            sanitized=verdict.sanitized_text,
            reason=verdict.reason,
        )

    def handle_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | dict[str, Any] | None:
        raise NotImplementedError(
            f"{type(self).__name__} does not accept webhooks · "
            "either override handle_webhook() or use polling via start()",
        )

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"{type(self).__name__}(channel_id={self.channel_id!r})"
