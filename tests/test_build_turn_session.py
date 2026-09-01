"""
Tests for ``build_turn_session`` Â· the helper that merges request
context + persisted thread metadata into a ``Session`` object.

The scope resolver is only as correct as the session metadata it gets, so silent breakage here would map to wrong-tier writes or missing team access.

Tests pin each metadata-priority case + the silent-degradation
path when the thread store doesn't know the thread yet.
"""

from __future__ import annotations

from typing import Any

from runtime.sensing.gateway.turn_session import build_turn_session

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Fixtures
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class _StubAgent:
    def __init__(self, agent_id: str = "coder") -> None:
        self.agent_id = agent_id
        self.capabilities: dict[str, Any] = {}


class _StubStore:
    """Minimal ThreadStateStore substitute.

    ``get(thread_id)`` returns a stored thread dict or None. Tests
    seed the store with whatever persisted metadata they want to
    exercise the merge logic against.
    """

    def __init__(self, threads: dict[str, dict[str, Any]] | None = None):
        self._threads = threads or {}

    def get(self, thread_id: str) -> dict[str, Any] | None:
        return self._threads.get(thread_id)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Default path Â· no overrides anywhere
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class TestDefaults:
    def test_empty_body_empty_store_gives_chat_mode(self):
        sess = build_turn_session(
            actor="user1",
            agent=_StubAgent(),
            thread_id="t1",
            body={},
            store=_StubStore(),
        )
        assert sess.actor == "user1"
        assert sess.thread_id == "t1"
        assert sess.conversation_id == "t1"
        assert sess.metadata["mode"] == "chat"
        assert "team_id" not in sess.metadata
        assert "extra_workspaces" not in sess.metadata
        assert "identity_lock_override" not in sess.metadata


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# body.context overrides win
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class TestBodyContextWins:
    def test_body_mode_overrides_stored_mode(self):
        """A client saying ``mode=team`` in the request wins over
        any persisted ``mode`` â€” the design choice that makes the
        Code-page mode-switcher work without rewriting thread
        state first."""
        store = _StubStore({"t1": {"metadata": {"mode": "chat"}}})
        sess = build_turn_session(
            actor="u",
            agent=_StubAgent(),
            thread_id="t1",
            body={"context": {"mode": "team", "team_id": "t_alpha"}},
            store=store,
        )
        assert sess.metadata["mode"] == "team"
        assert sess.metadata["team_id"] == "t_alpha"

    def test_body_team_id_overrides_stored(self):
        store = _StubStore({"t1": {"metadata": {"team_id": "old_team"}}})
        sess = build_turn_session(
            actor="u",
            agent=_StubAgent(),
            thread_id="t1",
            body={"context": {"team_id": "new_team"}},
            store=store,
        )
        assert sess.metadata["team_id"] == "new_team"

    def test_raw_identity_flag_sets_override(self):
        """``context.raw_identity=true`` writes the
        ``identity_lock_override=False`` key that the
        identity_filter checks."""
        sess = build_turn_session(
            actor="u",
            agent=_StubAgent(),
            thread_id="t1",
            body={"context": {"raw_identity": True}},
            store=_StubStore(),
        )
        assert sess.metadata["identity_lock_override"] is False

    def test_raw_identity_false_does_not_set_override(self):
        """Only the truthy case sets the key â€” absent = use
        whatever the filter's default is."""
        sess = build_turn_session(
            actor="u",
            agent=_StubAgent(),
            thread_id="t1",
            body={"context": {"raw_identity": False}},
            store=_StubStore(),
        )
        assert "identity_lock_override" not in sess.metadata


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Stored metadata fills in when body absent
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class TestStoredMetadataFallback:
    def test_stored_mode_used_when_body_empty(self):
        store = _StubStore({"t1": {"metadata": {"mode": "code"}}})
        sess = build_turn_session(
            actor="u",
            agent=_StubAgent(),
            thread_id="t1",
            body={},
            store=store,
        )
        assert sess.metadata["mode"] == "code"

    def test_stored_extra_workspaces_flow_through(self):
        """``extra_workspaces`` only lives on the persisted thread Â·
        body.context never carries it (would be annoying to resend
        every turn). Scope resolver reads it from session.metadata."""
        store = _StubStore(
            {
                "t1": {
                    "metadata": {
                        "mode": "code",
                        "extra_workspaces": ["/opt/proj", "/home/data"],
                    }
                },
            }
        )
        sess = build_turn_session(
            actor="u",
            agent=_StubAgent(),
            thread_id="t1",
            body={},
            store=store,
        )
        assert sess.metadata["extra_workspaces"] == [
            "/opt/proj",
            "/home/data",
        ]

    def test_extra_workspaces_invalid_entries_filtered(self):
        """A stale/corrupt entry (non-string, empty string) must
        not crash the caller â€” silently drop. Scope resolver does
        a second filter pass but defense in depth here."""
        store = _StubStore(
            {
                "t1": {
                    "metadata": {
                        "extra_workspaces": [
                            "/ok",
                            "",  # empty dropped
                            None,  # non-string dropped
                            123,  # non-string dropped
                            "/also_ok",
                        ],
                    }
                },
            }
        )
        sess = build_turn_session(
            actor="u",
            agent=_StubAgent(),
            thread_id="t1",
            body={},
            store=store,
        )
        assert sess.metadata["extra_workspaces"] == ["/ok", "/also_ok"]


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Graceful degradation Â· store doesn't know the thread
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class TestStoreMissThread:
    def test_unknown_thread_falls_back_to_defaults(self):
        """New threads Â· the store returns None Â· session still
        builds with sensible defaults."""
        sess = build_turn_session(
            actor="u",
            agent=_StubAgent(),
            thread_id="brand_new",
            body={},
            store=_StubStore(),  # empty
        )
        assert sess.metadata["mode"] == "chat"

    def test_store_keyerror_tolerated(self):
        """A store impl that raises KeyError on unknown (rather
        than returning None) must still work."""

        class _RaisingStore:
            def get(self, tid: str):
                raise KeyError(tid)

        sess = build_turn_session(
            actor="u",
            agent=_StubAgent(),
            thread_id="t",
            body={"context": {"mode": "team", "team_id": "t_x"}},
            store=_RaisingStore(),
        )
        assert sess.metadata["mode"] == "team"
        assert sess.metadata["team_id"] == "t_x"

    def test_store_without_get_method_tolerated(self):
        """An incomplete store stub (no ``get``) must also degrade
        gracefully â€” body context still fills mode/team_id."""

        class _BrokenStore:
            pass

        sess = build_turn_session(
            actor="u",
            agent=_StubAgent(),
            thread_id="t",
            body={"context": {"mode": "chat"}},
            store=_BrokenStore(),
        )
        assert sess.metadata["mode"] == "chat"
