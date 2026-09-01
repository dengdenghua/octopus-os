#!/usr/bin/env python3
"""Bounded, thread-safe notification history for the Echo session daemon."""

from __future__ import annotations

import html
import threading
import time
from collections import OrderedDict
from html.parser import HTMLParser
from typing import Any

MAX_NOTIFICATIONS = 100
MAX_APP_NAME_CHARS = 128
MAX_SUMMARY_CHARS = 512
MAX_BODY_CHARS = 4096
DEFAULT_EXPIRE_MILLISECONDS = 5000
MAX_EXPIRE_MILLISECONDS = 24 * 60 * 60 * 1000
MAX_NOTIFICATION_ID = (1 << 32) - 1


class _PlainTextParser(HTMLParser):
    """Accept the FDO body markup subset but retain only visible plain text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        if tag.lower() in {"br", "p", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"p", "div", "li"}:
            self.parts.append("\n")

    def get_text(self) -> str:
        return "".join(self.parts)


def _bounded_text(value: object, limit: int, *, markup: bool = False) -> str:
    text = str(value or "").replace("\x00", "").replace("\r\n", "\n")
    if markup:
        parser = _PlainTextParser()
        try:
            parser.feed(text)
            parser.close()
            text = parser.get_text()
        except (AssertionError, ValueError):
            # Malformed markup is still untrusted notification content. A
            # conservative tag-free fallback is preferable to rejecting the
            # entire D-Bus call and breaking the sending application.
            text = text.replace("<", " ").replace(">", " ")
        text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    text = "\n".join(line for line in lines if line).strip()
    return text[:limit]


def normalize_app_name(value: object) -> str:
    return _bounded_text(value, MAX_APP_NAME_CHARS) or "应用"


def normalize_summary(value: object) -> str:
    return _bounded_text(value, MAX_SUMMARY_CHARS)


def normalize_body(value: object) -> str:
    return _bounded_text(value, MAX_BODY_CHARS, markup=True)


def normalize_expire_timeout(value: object) -> int | None:
    """Return an expiry duration, or None when the notification is resident."""

    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = -1
    if timeout == 0:
        return None
    if timeout < 0:
        return DEFAULT_EXPIRE_MILLISECONDS
    return max(100, min(timeout, MAX_EXPIRE_MILLISECONDS))


class NotificationStore:
    """Store at most 100 safe notification summaries for one login session.

    Natural expiry closes the transient FDO notification but deliberately keeps
    its plain-text history entry. Explicit application/user dismissal removes
    the entry, which mirrors the behavior users expect from a notification
    center without claiming cross-login persistence.
    """

    def __init__(self, capacity: int = MAX_NOTIFICATIONS) -> None:
        if capacity < 1 or capacity > MAX_NOTIFICATIONS:
            raise ValueError("notification capacity is out of bounds")
        self.capacity = capacity
        self._entries: OrderedDict[int, dict[str, Any]] = OrderedDict()
        self._next_id = 1
        self._lock = threading.RLock()

    def _allocate_id(self) -> int:
        for _attempt in range(MAX_NOTIFICATION_ID):
            notification_id = self._next_id
            self._next_id = 1 if notification_id >= MAX_NOTIFICATION_ID else notification_id + 1
            if notification_id not in self._entries:
                return notification_id
        raise RuntimeError("notification id space exhausted")

    @staticmethod
    def _public(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": entry["id"],
            "appName": entry["appName"],
            "summary": entry["summary"],
            "body": entry["body"],
            "createdAt": entry["createdAt"],
            "updatedAt": entry["updatedAt"],
        }

    def notify(
        self,
        *,
        app_name: object,
        replaces_id: object,
        summary: object,
        body: object,
        expire_timeout: object,
        now_ms: int | None = None,
    ) -> tuple[int, list[int]]:
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        timeout = normalize_expire_timeout(expire_timeout)
        with self._lock:
            try:
                requested_id = int(replaces_id)
            except (TypeError, ValueError):
                requested_id = 0
            replacing = requested_id if requested_id in self._entries else 0
            notification_id = replacing or self._allocate_id()
            created_at = (
                int(self._entries[notification_id]["createdAt"])
                if replacing
                else now
            )
            entry = {
                "id": notification_id,
                "appName": normalize_app_name(app_name),
                "summary": normalize_summary(summary),
                "body": normalize_body(body),
                "createdAt": created_at,
                "updatedAt": now,
                "active": True,
                "expiresAt": None if timeout is None else now + timeout,
            }
            self._entries[notification_id] = entry
            self._entries.move_to_end(notification_id)
            evicted_active: list[int] = []
            while len(self._entries) > self.capacity:
                evicted_id, evicted = self._entries.popitem(last=False)
                if evicted["active"]:
                    evicted_active.append(evicted_id)
            return notification_id, evicted_active

    def list_notifications(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self._public(entry)
                for entry in reversed(tuple(self._entries.values()))
            ]

    def expire_due(self, now_ms: int | None = None) -> list[int]:
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        expired: list[int] = []
        with self._lock:
            for notification_id, entry in self._entries.items():
                expires_at = entry["expiresAt"]
                if entry["active"] and expires_at is not None and expires_at <= now:
                    entry["active"] = False
                    entry["expiresAt"] = None
                    expired.append(notification_id)
        return expired

    def close(self, notification_id: object) -> bool:
        removed, _was_active = self.close_with_state(notification_id)
        return removed

    def close_with_state(self, notification_id: object) -> tuple[bool, bool]:
        try:
            normalized_id = int(notification_id)
        except (TypeError, ValueError):
            return False, False
        with self._lock:
            entry = self._entries.pop(normalized_id, None)
            return entry is not None, bool(entry and entry["active"])

    def clear(self) -> list[int]:
        removed, _active = self.clear_with_state()
        return removed

    def clear_with_state(self) -> tuple[list[int], list[int]]:
        with self._lock:
            removed = list(self._entries)
            active = [
                notification_id
                for notification_id, entry in self._entries.items()
                if entry["active"]
            ]
            self._entries.clear()
            return removed, active
