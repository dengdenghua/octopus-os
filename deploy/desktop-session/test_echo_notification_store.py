#!/usr/bin/env python3
"""Portable tests for Echo's bounded desktop notification history."""

from __future__ import annotations

from echo_notification_store import (
    MAX_APP_NAME_CHARS,
    MAX_BODY_CHARS,
    MAX_SUMMARY_CHARS,
    NotificationStore,
    normalize_body,
)


def main() -> None:
    assert normalize_body("<b>Hello</b><br>world &amp; friends") == (
        "Hello\nworld & friends"
    )
    assert "<script" not in normalize_body("<script>alert(1)</script>safe")

    store = NotificationStore(capacity=2)
    first, evicted = store.notify(
        app_name="A" * 300,
        replaces_id=0,
        summary="S" * 900,
        body="B" * 9000,
        expire_timeout=-1,
        now_ms=1000,
    )
    assert first == 1 and evicted == []
    first_entry = store.list_notifications()[0]
    assert len(first_entry["appName"]) == MAX_APP_NAME_CHARS
    assert len(first_entry["summary"]) == MAX_SUMMARY_CHARS
    assert len(first_entry["body"]) == MAX_BODY_CHARS

    replaced, evicted = store.notify(
        app_name="Mail",
        replaces_id=first,
        summary="Updated",
        body="Replacement",
        expire_timeout=0,
        now_ms=2000,
    )
    assert replaced == first and evicted == []
    assert store.list_notifications()[0]["createdAt"] == 1000
    assert store.list_notifications()[0]["updatedAt"] == 2000
    assert store.expire_due(10_000) == []

    second, _ = store.notify(
        app_name="Files",
        replaces_id=0,
        summary="Copied",
        body="One file",
        expire_timeout=100,
        now_ms=3000,
    )
    assert store.expire_due(3099) == []
    assert store.expire_due(3100) == [second]
    # Natural popup expiry remains visible in history and is reported once.
    assert {item["id"] for item in store.list_notifications()} == {first, second}
    assert store.expire_due(5000) == []

    third, evicted = store.notify(
        app_name="Calendar",
        replaces_id=0,
        summary="Event",
        body="Now",
        expire_timeout=0,
        now_ms=4000,
    )
    assert third == 3
    assert evicted == [first]
    assert [item["id"] for item in store.list_notifications()] == [third, second]
    assert store.close_with_state(second) == (True, False)
    assert store.close(second) is False
    assert store.clear() == [third]
    assert store.list_notifications() == []

    print("Echo notification store tests passed")


if __name__ == "__main__":
    main()
