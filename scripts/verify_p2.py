#!/usr/bin/env python3
"""
Echo Native Session API v2 Manual Verification Script

Quick smoke test for P2 features:
- Session-query (FTS5 search)
- Feedback (thumbs up/down)
- Export (Markdown)
"""

import tempfile
from pathlib import Path

from runtime.memory.threads import ThreadStateStore


def test_p2_features():
    """Test all P2 features in isolation."""

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)

        # Initialize store with P2 features enabled
        store = ThreadStateStore(
            path=data_dir / "threads.jsonl",
            search_enabled=True,
            feedback_enabled=True,
        )

        # Create test thread
        thread = store.create(
            metadata={"owner_actor_id": "test_user"},
            values={
                "messages": [
                    {"role": "user", "content": "How do I fix authentication bug?"},
                    {"role": "assistant", "content": "Check your JWT configuration."},
                    {"role": "user", "content": "What about database connection?"},
                    {"role": "assistant", "content": "Verify your connection string."},
                ]
            },
        )
        thread_id = thread["thread_id"]

        print(f"✓ Created thread: {thread_id}")

        # Test 1: Search
        results = store.search_threads("authentication")
        assert len(results) == 1
        assert results[0].thread_id == thread_id
        assert "authentication" in results[0].snippet.lower()
        print(f"✓ Search found 1 result (rank: {results[0].rank:.2f})")

        # Test 2: Feedback
        feedback1 = store.add_message_feedback(
            thread_id=thread_id,
            message_index=1,
            feedback_type="thumbs_up",
            tags=["helpful", "accurate"],
            comment="This fixed my issue!",
        )
        assert feedback1.feedback_type == "thumbs_up"
        assert len(feedback1.tags) == 2
        print("✓ Added positive feedback to message 1")

        feedback2 = store.add_message_feedback(
            thread_id=thread_id,
            message_index=3,
            feedback_type="thumbs_down",
            tags=["incomplete"],
            comment="Need more details",
        )
        assert feedback2.feedback_type == "thumbs_down"
        print("✓ Added negative feedback to message 3")

        # Test 3: Feedback Stats
        stats = store.get_feedback_stats(thread_id)
        assert stats["total"] == 2
        assert stats["thumbs_up"] == 1
        assert stats["thumbs_down"] == 1
        assert len(stats["messages_with_feedback"]) == 2
        assert stats["tags"]["helpful"] == 1
        assert stats["tags"]["incomplete"] == 1
        print(f"✓ Stats: {stats['thumbs_up']} up, {stats['thumbs_down']} down")

        # Test 4: Export
        markdown = store.export_thread_markdown(thread_id)
        assert markdown is not None
        assert "thread_id:" in markdown
        assert "authentication bug" in markdown
        assert "JWT configuration" in markdown
        assert len(markdown.split("\n")) > 10
        print(f"✓ Export generated {len(markdown)} chars of Markdown")

        # Test 5: Multiple threads search
        store.create(
            metadata={"owner_actor_id": "test_user"},
            values={
                "messages": [
                    {"role": "user", "content": "How to configure database?"},
                    {"role": "assistant", "content": "Use DATABASE_URL environment variable."},
                ]
            },
        )

        results = store.search_threads("database")
        assert len(results) == 2  # Both threads mention database
        print(f"✓ Search found {len(results)} results for 'database'")

        # Test 6: Search with no results
        results = store.search_threads("nonexistent xyz123")
        assert len(results) == 0
        print("✓ Search correctly returns 0 results for gibberish")

        print("\n✅ All P2 features working correctly!")


if __name__ == "__main__":
    test_p2_features()


