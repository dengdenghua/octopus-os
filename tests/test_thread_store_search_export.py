"""Integration tests for ThreadStateStore search and export.

Echo Native session-query integration tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.memory.threads.store import ThreadStateStore


class TestThreadStoreSearch:
    """Test search integration with ThreadStateStore."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> ThreadStateStore:
        """Create a store with search enabled."""
        return ThreadStateStore(
            per_agent_base=tmp_path,
            search_enabled=True,
        )

    def test_search_after_create(self, store: ThreadStateStore) -> None:
        """Search finds threads after creation."""
        thread = store.create(
            values={
                "title": "Authentication Bug",
                "messages": [{"role": "user", "content": "Fix the login authentication issue"}],
            }
        )

        results = store.search_threads("authentication")
        assert len(results) == 1
        assert results[0].thread_id == thread["thread_id"]
        assert results[0].title == "Authentication Bug"

    def test_search_after_update(self, store: ThreadStateStore) -> None:
        """Search finds updated content."""
        thread = store.create(
            values={
                "title": "Empty Thread",
                "messages": [],
            }
        )

        # Update with new messages
        store.update_state(
            thread["thread_id"],
            values={
                "title": "Database Issue",
                "messages": [
                    {"role": "user", "content": "Connection to database failed"},
                    {"role": "assistant", "content": "Let me check the config"},
                ],
            },
        )

        # Old title should not be found
        results = store.search_threads("Empty")
        assert len(results) == 0

        # New content should be found
        results = store.search_threads("database")
        assert len(results) == 1
        assert results[0].title == "Database Issue"

    def test_search_multiple_threads(self, store: ThreadStateStore) -> None:
        """Search across multiple threads."""
        store.create(
            values={
                "title": "Auth Bug",
                "messages": [{"role": "user", "content": "authentication problem"}],
            }
        )

        store.create(
            values={
                "title": "Database Issue",
                "messages": [{"role": "user", "content": "database connection failed"}],
            }
        )

        store.create(
            values={
                "title": "Auth Token",
                "messages": [{"role": "user", "content": "token authentication expired"}],
            }
        )

        # Search for "authentication" should return 2 threads
        results = store.search_threads("authentication")
        assert len(results) == 2

        # Search for "database" should return 1 thread
        results = store.search_threads("database")
        assert len(results) == 1

    def test_search_filter_by_agent(self, store: ThreadStateStore) -> None:
        """Filter search by agent_id."""
        store.create(
            metadata={"agent": "agent_a"},
            values={
                "title": "Agent A Thread",
                "messages": [{"role": "user", "content": "test message"}],
            },
        )

        store.create(
            metadata={"agent": "agent_b"},
            values={
                "title": "Agent B Thread",
                "messages": [{"role": "user", "content": "test message"}],
            },
        )

        results = store.search_threads("test", agent_id="agent_a")
        assert len(results) == 1
        assert results[0].agent_id == "agent_a"

    def test_search_filter_by_team(self, store: ThreadStateStore) -> None:
        """Filter search by team_id."""
        store.create(
            metadata={"team_id": "team_1"},
            values={
                "title": "Team Thread",
                "messages": [{"role": "user", "content": "test message"}],
            },
        )

        store.create(
            metadata={"team_id": "team_2"},
            values={
                "title": "Other Team",
                "messages": [{"role": "user", "content": "test message"}],
            },
        )

        results = store.search_threads("test", team_id="team_1")
        assert len(results) == 1
        assert results[0].team_id == "team_1"

    def test_search_disabled(self, tmp_path: Path) -> None:
        """Search returns empty when disabled."""
        store = ThreadStateStore(
            per_agent_base=tmp_path,
            search_enabled=False,
        )

        store.create(
            values={
                "title": "Test",
                "messages": [{"role": "user", "content": "test content"}],
            }
        )

        results = store.search_threads("test")
        assert len(results) == 0


class TestThreadStoreExport:
    """Test export integration with ThreadStateStore."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> ThreadStateStore:
        """Create a store."""
        return ThreadStateStore(per_agent_base=tmp_path)

    def test_export_simple_thread(self, store: ThreadStateStore) -> None:
        """Export a simple thread to markdown."""
        thread = store.create(
            values={
                "title": "Simple Thread",
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there!"},
                ],
            }
        )

        markdown = store.export_thread_markdown(thread["thread_id"])
        assert markdown is not None
        assert "# Simple Thread" in markdown
        assert f"thread_id: {thread['thread_id']}" in markdown
        assert "Hello" in markdown
        assert "Hi there!" in markdown

    def test_export_with_metadata(self, store: ThreadStateStore) -> None:
        """Export includes agent and team metadata."""
        thread = store.create(
            metadata={"agent": "agent_a", "team_id": "team_1"},
            values={
                "title": "Test Thread",
                "messages": [{"role": "user", "content": "Test"}],
            },
        )

        markdown = store.export_thread_markdown(thread["thread_id"])
        assert markdown is not None
        assert "agent_id: agent_a" in markdown
        assert "team_id: team_1" in markdown

    def test_export_nonexistent_thread(self, store: ThreadStateStore) -> None:
        """Export returns None for nonexistent thread."""
        markdown = store.export_thread_markdown("nonexistent_thread_id")
        assert markdown is None

    def test_export_tool_calls(self, store: ThreadStateStore) -> None:
        """Export preserves tool call structure."""
        thread = store.create(
            values={
                "title": "Tool Usage",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Let me search for that."},
                            {
                                "type": "tool_use",
                                "name": "search_files",
                                "input": {"query": "auth"},
                            },
                        ],
                    }
                ],
            }
        )

        markdown = store.export_thread_markdown(thread["thread_id"])
        assert markdown is not None
        assert "Let me search for that." in markdown
        assert "**Tool Call:** `search_files`" in markdown
        assert '"query": "auth"' in markdown


class TestThreadStoreDelete:
    """Test delete with search index cleanup."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> ThreadStateStore:
        """Create a store with search enabled."""
        return ThreadStateStore(
            per_agent_base=tmp_path,
            search_enabled=True,
        )

    def test_delete_removes_from_search(self, store: ThreadStateStore) -> None:
        """Deleting a thread removes it from search index."""
        thread = store.create(
            values={
                "title": "Test Thread",
                "messages": [{"role": "user", "content": "test content"}],
            }
        )

        # Verify it's searchable
        results = store.search_threads("test")
        assert len(results) == 1

        # Delete the thread
        store.delete_thread(thread["thread_id"])

        # Verify it's no longer searchable
        results = store.search_threads("test")
        assert len(results) == 0

    def test_delete_removes_from_memory(self, store: ThreadStateStore) -> None:
        """Deleting a thread removes it from memory."""
        thread = store.create(
            values={
                "title": "Test Thread",
                "messages": [{"role": "user", "content": "test"}],
            }
        )

        thread_id = thread["thread_id"]

        # Verify it exists
        retrieved = store.get(thread_id)
        assert retrieved is not None

        # Delete it
        store.delete_thread(thread_id)

        # Verify it's gone
        retrieved = store.get(thread_id)
        assert retrieved is None


class TestThreadStoreSearchPersistence:
    """Test search index persistence across store instances."""

    def test_search_persists_across_instances(self, tmp_path: Path) -> None:
        """Search index persists when store is closed and reopened."""
        # Create store and add thread
        store1 = ThreadStateStore(
            per_agent_base=tmp_path,
            search_enabled=True,
        )

        thread = store1.create(
            values={
                "title": "Persistent Thread",
                "messages": [{"role": "user", "content": "persistent content"}],
            }
        )

        thread_id = thread["thread_id"]

        # Verify search works
        results = store1.search_threads("persistent")
        assert len(results) == 1

        # Close and reopen store
        del store1

        store2 = ThreadStateStore(
            per_agent_base=tmp_path,
            search_enabled=True,
        )

        # Search should still work
        results = store2.search_threads("persistent")
        assert len(results) == 1
        assert results[0].thread_id == thread_id

