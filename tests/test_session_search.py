"""Tests for session search index.

Echo Native session-query tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.memory.threads.session_search import SessionSearchIndex


class TestSessionSearchIndex:
    """Test basic indexing and search operations."""

    @pytest.fixture
    def search_index(self, tmp_path: Path) -> SessionSearchIndex:
        """Create a temporary search index."""
        db_path = tmp_path / "search.db"
        return SessionSearchIndex(db_path)

    def test_index_and_search_simple(self, search_index: SessionSearchIndex) -> None:
        """Index a thread and search for it."""
        search_index.index_thread(
            thread_id="thread_1",
            title="Authentication Bug",
            messages=[
                {"role": "user", "content": "Fix the login authentication issue"},
                {"role": "assistant", "content": "I'll help you fix the authentication bug"},
            ],
        )

        results = search_index.search("authentication")
        assert len(results) == 1
        assert results[0].thread_id == "thread_1"
        assert results[0].title == "Authentication Bug"
        assert "authentication" in results[0].snippet.lower()

    def test_search_no_results(self, search_index: SessionSearchIndex) -> None:
        """Search for non-existent content."""
        search_index.index_thread(
            thread_id="thread_1",
            title="Test Thread",
            messages=[{"role": "user", "content": "Hello world"}],
        )

        results = search_index.search("nonexistent")
        assert len(results) == 0

    def test_search_empty_query(self, search_index: SessionSearchIndex) -> None:
        """Empty query returns no results."""
        results = search_index.search("")
        assert len(results) == 0

        results = search_index.search("   ")
        assert len(results) == 0

    def test_multiple_threads(self, search_index: SessionSearchIndex) -> None:
        """Index multiple threads and search."""
        search_index.index_thread(
            thread_id="thread_1",
            title="Auth Bug",
            messages=[{"role": "user", "content": "authentication problem"}],
        )

        search_index.index_thread(
            thread_id="thread_2",
            title="Database Issue",
            messages=[{"role": "user", "content": "database connection failed"}],
        )

        search_index.index_thread(
            thread_id="thread_3",
            title="Auth Token",
            messages=[{"role": "user", "content": "token authentication expired"}],
        )

        # Search for "authentication" should return thread_1 and thread_3
        results = search_index.search("authentication")
        assert len(results) == 2
        thread_ids = {r.thread_id for r in results}
        assert thread_ids == {"thread_1", "thread_3"}

        # Search for "database" should return only thread_2
        results = search_index.search("database")
        assert len(results) == 1
        assert results[0].thread_id == "thread_2"

    def test_update_thread(self, search_index: SessionSearchIndex) -> None:
        """Updating a thread replaces old content."""
        # Initial index
        search_index.index_thread(
            thread_id="thread_1",
            title="Original Title",
            messages=[{"role": "user", "content": "original content"}],
        )

        results = search_index.search("original")
        assert len(results) == 1

        # Update with new content
        search_index.index_thread(
            thread_id="thread_1",
            title="Updated Title",
            messages=[{"role": "user", "content": "updated content"}],
        )

        # Old content should not be found
        results = search_index.search("original")
        assert len(results) == 0

        # New content should be found
        results = search_index.search("updated")
        assert len(results) == 1
        assert results[0].title == "Updated Title"

    def test_delete_thread(self, search_index: SessionSearchIndex) -> None:
        """Deleting a thread removes it from search."""
        search_index.index_thread(
            thread_id="thread_1",
            title="Test",
            messages=[{"role": "user", "content": "test content"}],
        )

        results = search_index.search("test")
        assert len(results) == 1

        search_index.delete_thread("thread_1")

        results = search_index.search("test")
        assert len(results) == 0

    def test_multipart_content(self, search_index: SessionSearchIndex) -> None:
        """Handle multipart message content."""
        search_index.index_thread(
            thread_id="thread_1",
            title="Multipart Message",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "First part"},
                        {"type": "text", "text": "Second part"},
                        {"type": "image", "url": "http://example.com/img.png"},
                    ],
                }
            ],
        )

        # Should find both text parts
        results = search_index.search("First")
        assert len(results) == 1

        results = search_index.search("Second")
        assert len(results) == 1


class TestSearchFilters:
    """Test search filtering by agent, team, dates."""

    @pytest.fixture
    def search_index(self, tmp_path: Path) -> SessionSearchIndex:
        """Create index with sample data."""
        db_path = tmp_path / "search.db"
        index = SessionSearchIndex(db_path)

        index.index_thread(
            thread_id="thread_1",
            title="Agent A Thread",
            messages=[{"role": "user", "content": "test message"}],
            agent_id="agent_a",
            updated_at="2026-08-01T10:00:00Z",
        )

        index.index_thread(
            thread_id="thread_2",
            title="Agent B Thread",
            messages=[{"role": "user", "content": "test message"}],
            agent_id="agent_b",
            updated_at="2026-08-10T10:00:00Z",
        )

        index.index_thread(
            thread_id="thread_3",
            title="Team Thread",
            messages=[{"role": "user", "content": "test message"}],
            team_id="team_1",
            updated_at="2026-08-15T10:00:00Z",
        )

        return index

    def test_filter_by_agent(self, search_index: SessionSearchIndex) -> None:
        """Filter search results by agent_id."""
        results = search_index.search("test", agent_id="agent_a")
        assert len(results) == 1
        assert results[0].thread_id == "thread_1"
        assert results[0].agent_id == "agent_a"

        results = search_index.search("test", agent_id="agent_b")
        assert len(results) == 1
        assert results[0].thread_id == "thread_2"

    def test_filter_by_team(self, search_index: SessionSearchIndex) -> None:
        """Filter search results by team_id."""
        results = search_index.search("test", team_id="team_1")
        assert len(results) == 1
        assert results[0].thread_id == "thread_3"
        assert results[0].team_id == "team_1"

    def test_filter_by_date_after(self, search_index: SessionSearchIndex) -> None:
        """Filter search results by date (after)."""
        results = search_index.search("test", after="2026-08-05T00:00:00Z")
        assert len(results) == 2
        thread_ids = {r.thread_id for r in results}
        assert thread_ids == {"thread_2", "thread_3"}

    def test_filter_by_date_before(self, search_index: SessionSearchIndex) -> None:
        """Filter search results by date (before)."""
        results = search_index.search("test", before="2026-08-05T00:00:00Z")
        assert len(results) == 1
        assert results[0].thread_id == "thread_1"

    def test_filter_combined(self, search_index: SessionSearchIndex) -> None:
        """Combine multiple filters."""
        results = search_index.search(
            "test",
            agent_id="agent_b",
            after="2026-08-05T00:00:00Z",
        )
        assert len(results) == 1
        assert results[0].thread_id == "thread_2"

    def test_limit(self, search_index: SessionSearchIndex) -> None:
        """Respect limit parameter."""
        # All three threads match "test"
        results = search_index.search("test", limit=2)
        assert len(results) == 2


class TestFTS5Features:
    """Test FTS5-specific features like phrase search, boolean operators."""

    @pytest.fixture
    def search_index(self, tmp_path: Path) -> SessionSearchIndex:
        """Create index with sample data."""
        db_path = tmp_path / "search.db"
        index = SessionSearchIndex(db_path)

        index.index_thread(
            thread_id="thread_1",
            title="Quick Brown Fox",
            messages=[{"role": "user", "content": "The quick brown fox jumps over the lazy dog"}],
        )

        index.index_thread(
            thread_id="thread_2",
            title="Lazy Cat",
            messages=[{"role": "user", "content": "The lazy cat sleeps all day"}],
        )

        return index

    def test_phrase_search(self, search_index: SessionSearchIndex) -> None:
        """Search for exact phrase."""
        results = search_index.search('"quick brown"')
        assert len(results) == 1
        assert results[0].thread_id == "thread_1"

        results = search_index.search('"lazy dog"')
        assert len(results) == 1
        assert results[0].thread_id == "thread_1"

    def test_boolean_and(self, search_index: SessionSearchIndex) -> None:
        """Search with AND operator."""
        results = search_index.search("lazy AND dog")
        assert len(results) == 1
        assert results[0].thread_id == "thread_1"

        results = search_index.search("lazy AND cat")
        assert len(results) == 1
        assert results[0].thread_id == "thread_2"

    def test_boolean_or(self, search_index: SessionSearchIndex) -> None:
        """Search with OR operator."""
        results = search_index.search("fox OR cat")
        assert len(results) == 2
        thread_ids = {r.thread_id for r in results}
        assert thread_ids == {"thread_1", "thread_2"}

    def test_boolean_not(self, search_index: SessionSearchIndex) -> None:
        """Search with NOT operator."""
        results = search_index.search("lazy NOT dog")
        assert len(results) == 1
        assert results[0].thread_id == "thread_2"


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_nonexistent_db_path_creates_directory(self, tmp_path: Path) -> None:
        """Database directory is created if it doesn't exist."""
        nested_path = tmp_path / "deep" / "nested" / "path" / "search.db"
        index = SessionSearchIndex(nested_path)

        assert nested_path.parent.exists()
        index.index_thread(
            thread_id="thread_1",
            title="Test",
            messages=[{"role": "user", "content": "test"}],
        )

    def test_empty_messages(self, tmp_path: Path) -> None:
        """Handle thread with no messages."""
        index = SessionSearchIndex(tmp_path / "search.db")
        index.index_thread(
            thread_id="thread_1",
            title="Empty Thread",
            messages=[],
        )

        results = index.search("Empty")
        assert len(results) == 1
        assert results[0].title == "Empty Thread"

    def test_malformed_content(self, tmp_path: Path) -> None:
        """Handle malformed message content gracefully."""
        index = SessionSearchIndex(tmp_path / "search.db")
        index.index_thread(
            thread_id="thread_1",
            title="Malformed",
            messages=[
                {"role": "user"},  # Missing content
                {"role": "assistant", "content": None},  # Null content
                {"role": "user", "content": 123},  # Non-string content
            ],
        )

        # Should not crash, just skip malformed parts
        results = index.search("Malformed")
        assert len(results) == 1

    def test_special_characters_in_query(self, tmp_path: Path) -> None:
        """Handle special characters in search query."""
        index = SessionSearchIndex(tmp_path / "search.db")
        index.index_thread(
            thread_id="thread_1",
            title="Special Chars",
            messages=[{"role": "user", "content": "Test with @#$% special chars"}],
        )

        # Basic search should work
        results = index.search("special")
        assert len(results) == 1

    def test_optimize_doesnt_crash(self, tmp_path: Path) -> None:
        """Optimize operation completes without error."""
        index = SessionSearchIndex(tmp_path / "search.db")
        index.index_thread(
            thread_id="thread_1",
            title="Test",
            messages=[{"role": "user", "content": "test"}],
        )

        # Should not raise
        index.optimize()

    def test_close_and_reopen(self, tmp_path: Path) -> None:
        """Close and reopen database preserves data."""
        db_path = tmp_path / "search.db"

        # Write data
        index1 = SessionSearchIndex(db_path)
        index1.index_thread(
            thread_id="thread_1",
            title="Test",
            messages=[{"role": "user", "content": "test content"}],
        )
        index1.close()

        # Reopen and read
        index2 = SessionSearchIndex(db_path)
        results = index2.search("test")
        assert len(results) == 1
        assert results[0].thread_id == "thread_1"

