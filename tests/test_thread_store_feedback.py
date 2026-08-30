"""Integration tests for ThreadStateStore feedback system.

Echo Native feedback integration tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.memory.threads.store import ThreadStateStore


class TestThreadStoreFeedback:
    """Test feedback integration with ThreadStateStore."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> ThreadStateStore:
        """Create a store with feedback enabled."""
        return ThreadStateStore(
            per_agent_base=tmp_path,
            feedback_enabled=True,
        )

    def test_add_feedback_thumbs_up(self, store: ThreadStateStore) -> None:
        """Add thumbs up feedback to a message."""
        thread = store.create(
            values={
                "title": "Test Thread",
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there!"},
                ],
            }
        )

        feedback = store.add_message_feedback(
            thread_id=thread["thread_id"],
            message_index=1,  # Assistant message
            feedback_type="thumbs_up",
            tags=["helpful"],
            comment="Great response!",
        )

        assert feedback is not None
        assert feedback.thread_id == thread["thread_id"]
        assert feedback.message_index == 1
        assert feedback.feedback_type == "thumbs_up"
        assert feedback.tags == ("helpful",)
        assert feedback.comment == "Great response!"

    def test_add_feedback_thumbs_down(self, store: ThreadStateStore) -> None:
        """Add thumbs down feedback to a message."""
        thread = store.create(
            values={
                "title": "Test Thread",
                "messages": [
                    {"role": "user", "content": "Fix the bug"},
                    {"role": "assistant", "content": "Here's the fix..."},
                ],
            }
        )

        feedback = store.add_message_feedback(
            thread_id=thread["thread_id"],
            message_index=1,
            feedback_type="thumbs_down",
            tags=["inaccurate", "incomplete"],
            comment="Didn't actually fix the bug",
        )

        assert feedback is not None
        assert feedback.feedback_type == "thumbs_down"
        assert feedback.tags == ("inaccurate", "incomplete")

    def test_get_message_feedback_all(self, store: ThreadStateStore) -> None:
        """Get all feedback for a thread."""
        thread = store.create(
            values={
                "title": "Test Thread",
                "messages": [
                    {"role": "user", "content": "Q1"},
                    {"role": "assistant", "content": "A1"},
                    {"role": "user", "content": "Q2"},
                    {"role": "assistant", "content": "A2"},
                ],
            }
        )

        thread_id = thread["thread_id"]

        # Add feedback to different messages
        store.add_message_feedback(
            thread_id=thread_id,
            message_index=1,
            feedback_type="thumbs_up",
        )

        store.add_message_feedback(
            thread_id=thread_id,
            message_index=3,
            feedback_type="thumbs_down",
        )

        # Get all feedback
        feedbacks = store.get_message_feedback(thread_id)
        assert len(feedbacks) == 2
        assert feedbacks[0].message_index == 1
        assert feedbacks[1].message_index == 3

    def test_get_message_feedback_specific(self, store: ThreadStateStore) -> None:
        """Get feedback for a specific message."""
        thread = store.create(
            values={
                "title": "Test Thread",
                "messages": [
                    {"role": "user", "content": "Q1"},
                    {"role": "assistant", "content": "A1"},
                    {"role": "user", "content": "Q2"},
                    {"role": "assistant", "content": "A2"},
                ],
            }
        )

        thread_id = thread["thread_id"]

        store.add_message_feedback(
            thread_id=thread_id,
            message_index=1,
            feedback_type="thumbs_up",
        )

        store.add_message_feedback(
            thread_id=thread_id,
            message_index=3,
            feedback_type="thumbs_down",
        )

        # Get only message 1 feedback
        feedbacks = store.get_message_feedback(thread_id, message_index=1)
        assert len(feedbacks) == 1
        assert feedbacks[0].message_index == 1
        assert feedbacks[0].feedback_type == "thumbs_up"

    def test_get_feedback_stats(self, store: ThreadStateStore) -> None:
        """Get feedback statistics for a thread."""
        thread = store.create(
            values={
                "title": "Test Thread",
                "messages": [
                    {"role": "user", "content": "Q1"},
                    {"role": "assistant", "content": "A1"},
                    {"role": "user", "content": "Q2"},
                    {"role": "assistant", "content": "A2"},
                ],
            }
        )

        thread_id = thread["thread_id"]

        store.add_message_feedback(
            thread_id=thread_id,
            message_index=1,
            feedback_type="thumbs_up",
            tags=["helpful"],
        )

        store.add_message_feedback(
            thread_id=thread_id,
            message_index=3,
            feedback_type="thumbs_down",
            tags=["inaccurate", "too_verbose"],
        )

        stats = store.get_feedback_stats(thread_id)
        assert stats["total"] == 2
        assert stats["thumbs_up"] == 1
        assert stats["thumbs_down"] == 1
        assert stats["messages_with_feedback"] == [1, 3]
        assert stats["tags"]["helpful"] == 1
        assert stats["tags"]["inaccurate"] == 1
        assert stats["tags"]["too_verbose"] == 1

    def test_multiple_feedback_same_message(self, store: ThreadStateStore) -> None:
        """Multiple users can give feedback on same message."""
        thread = store.create(
            values={
                "title": "Test Thread",
                "messages": [
                    {"role": "user", "content": "Question"},
                    {"role": "assistant", "content": "Answer"},
                ],
            }
        )

        thread_id = thread["thread_id"]

        # User 1 gives thumbs up
        store.add_message_feedback(
            thread_id=thread_id,
            message_index=1,
            feedback_type="thumbs_up",
            user_id="user_1",
        )

        # User 2 gives thumbs down
        store.add_message_feedback(
            thread_id=thread_id,
            message_index=1,
            feedback_type="thumbs_down",
            user_id="user_2",
        )

        feedbacks = store.get_message_feedback(thread_id, message_index=1)
        assert len(feedbacks) == 2
        assert feedbacks[0].user_id == "user_1"
        assert feedbacks[1].user_id == "user_2"

    def test_feedback_disabled(self, tmp_path: Path) -> None:
        """Feedback operations return None/empty when disabled."""
        store = ThreadStateStore(
            per_agent_base=tmp_path,
            feedback_enabled=False,
        )

        thread = store.create(
            values={
                "title": "Test Thread",
                "messages": [{"role": "assistant", "content": "Test"}],
            }
        )

        thread_id = thread["thread_id"]

        # Add feedback returns None
        feedback = store.add_message_feedback(
            thread_id=thread_id,
            message_index=0,
            feedback_type="thumbs_up",
        )
        assert feedback is None

        # Get feedback returns empty
        feedbacks = store.get_message_feedback(thread_id)
        assert feedbacks == []

        # Stats returns zeros
        stats = store.get_feedback_stats(thread_id)
        assert stats["total"] == 0


class TestRLHFExportIntegration:
    """Test RLHF export integration."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> ThreadStateStore:
        """Create store with multiple threads and feedback."""
        store = ThreadStateStore(
            per_agent_base=tmp_path,
            feedback_enabled=True,
        )

        # Thread 1: 2 feedbacks
        thread1 = store.create(
            values={
                "title": "Thread 1",
                "messages": [
                    {"role": "user", "content": "Q"},
                    {"role": "assistant", "content": "A"},
                ],
            }
        )

        store.add_message_feedback(
            thread_id=thread1["thread_id"],
            message_index=1,
            feedback_type="thumbs_up",
            tags=["helpful"],
        )

        store.add_message_feedback(
            thread_id=thread1["thread_id"],
            message_index=1,
            feedback_type="thumbs_down",
            tags=["incomplete"],
        )

        # Thread 2: 1 feedback
        thread2 = store.create(
            values={
                "title": "Thread 2",
                "messages": [{"role": "assistant", "content": "Test"}],
            }
        )

        store.add_message_feedback(
            thread_id=thread2["thread_id"],
            message_index=0,
            feedback_type="thumbs_up",
        )

        return store

    def test_export_rlhf_all(self, store: ThreadStateStore, tmp_path: Path) -> None:
        """Export all feedback as RLHF dataset."""
        output = tmp_path / "rlhf_dataset.jsonl"
        count = store.export_rlhf_dataset(output)

        assert count == 3  # 2 + 1
        assert output.exists()

    def test_export_rlhf_filtered(self, store: ThreadStateStore, tmp_path: Path) -> None:
        """Export filtered RLHF dataset."""
        output = tmp_path / "rlhf_dataset.jsonl"

        # Only thumbs up
        count = store.export_rlhf_dataset(output, feedback_type_filter="thumbs_up")
        assert count == 2

        # Min feedback count
        output2 = tmp_path / "rlhf_dataset2.jsonl"
        count = store.export_rlhf_dataset(output2, min_feedback_count=2)
        assert count == 2  # Only thread 1

    def test_export_rlhf_disabled(self, tmp_path: Path) -> None:
        """Export returns 0 when feedback is disabled."""
        store = ThreadStateStore(
            per_agent_base=tmp_path,
            feedback_enabled=False,
        )

        output = tmp_path / "rlhf_dataset.jsonl"
        count = store.export_rlhf_dataset(output)
        assert count == 0


class TestFeedbackPersistence:
    """Test feedback persistence across store instances."""

    def test_feedback_persists(self, tmp_path: Path) -> None:
        """Feedback persists when store is closed and reopened."""
        # Create store and add feedback
        store1 = ThreadStateStore(
            per_agent_base=tmp_path,
            feedback_enabled=True,
        )

        thread = store1.create(
            values={
                "title": "Test Thread",
                "messages": [{"role": "assistant", "content": "Test"}],
            }
        )

        thread_id = thread["thread_id"]

        store1.add_message_feedback(
            thread_id=thread_id,
            message_index=0,
            feedback_type="thumbs_up",
            tags=["helpful"],
            comment="Great!",
        )

        del store1

        # Reopen and verify
        store2 = ThreadStateStore(
            per_agent_base=tmp_path,
            feedback_enabled=True,
        )

        feedbacks = store2.get_message_feedback(thread_id)
        assert len(feedbacks) == 1
        assert feedbacks[0].feedback_type == "thumbs_up"
        assert feedbacks[0].tags == ("helpful",)
        assert feedbacks[0].comment == "Great!"

