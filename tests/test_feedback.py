"""Tests for message feedback system.

Echo Native feedback system tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.memory.threads.feedback import (
    FEEDBACK_TAGS,
    FeedbackStore,
    MessageFeedback,
)


class TestMessageFeedback:
    """Test MessageFeedback dataclass."""

    def test_create_thumbs_up(self) -> None:
        """Create thumbs up feedback."""
        feedback = MessageFeedback(
            thread_id="thread_1",
            message_index=5,
            feedback_type="thumbs_up",
            tags=("helpful",),
            comment="Great explanation!",
            timestamp="2026-08-14T10:00:00Z",
            user_id="user_1",
        )

        assert feedback.thread_id == "thread_1"
        assert feedback.message_index == 5
        assert feedback.feedback_type == "thumbs_up"
        assert feedback.tags == ("helpful",)
        assert feedback.comment == "Great explanation!"

    def test_create_thumbs_down(self) -> None:
        """Create thumbs down feedback."""
        feedback = MessageFeedback(
            thread_id="thread_1",
            message_index=3,
            feedback_type="thumbs_down",
            tags=("inaccurate", "too_verbose"),
            comment="Code had bugs",
            timestamp="2026-08-14T10:00:00Z",
        )

        assert feedback.feedback_type == "thumbs_down"
        assert feedback.tags == ("inaccurate", "too_verbose")

    def test_to_dict(self) -> None:
        """Serialize to dictionary."""
        feedback = MessageFeedback(
            thread_id="thread_1",
            message_index=2,
            feedback_type="thumbs_up",
            tags=("helpful",),
            comment="Thanks!",
            timestamp="2026-08-14T10:00:00Z",
            user_id="user_1",
        )

        data = feedback.to_dict()
        assert data["thread_id"] == "thread_1"
        assert data["message_index"] == 2
        assert data["feedback_type"] == "thumbs_up"
        assert data["tags"] == ["helpful"]
        assert data["comment"] == "Thanks!"
        assert data["user_id"] == "user_1"

    def test_from_dict(self) -> None:
        """Deserialize from dictionary."""
        data = {
            "thread_id": "thread_1",
            "message_index": 2,
            "feedback_type": "thumbs_up",
            "tags": ["helpful"],
            "comment": "Thanks!",
            "timestamp": "2026-08-14T10:00:00Z",
            "user_id": "user_1",
        }

        feedback = MessageFeedback.from_dict(data)
        assert feedback.thread_id == "thread_1"
        assert feedback.message_index == 2
        assert feedback.feedback_type == "thumbs_up"
        assert feedback.tags == ("helpful",)

    def test_from_dict_optional_fields(self) -> None:
        """Deserialize with optional fields missing."""
        data = {
            "thread_id": "thread_1",
            "message_index": 2,
            "feedback_type": "thumbs_up",
            "timestamp": "2026-08-14T10:00:00Z",
        }

        feedback = MessageFeedback.from_dict(data)
        assert feedback.tags == ()
        assert feedback.comment == ""
        assert feedback.user_id is None


class TestFeedbackStore:
    """Test FeedbackStore basic operations."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> FeedbackStore:
        """Create a temporary feedback store."""
        return FeedbackStore(tmp_path)

    def test_add_feedback_thumbs_up(self, store: FeedbackStore) -> None:
        """Add thumbs up feedback."""
        feedback = store.add_feedback(
            thread_id="thread_1",
            message_index=5,
            feedback_type="thumbs_up",
            tags=["helpful"],
            comment="Great!",
            user_id="user_1",
        )

        assert feedback.thread_id == "thread_1"
        assert feedback.message_index == 5
        assert feedback.feedback_type == "thumbs_up"
        assert feedback.tags == ("helpful",)
        assert feedback.comment == "Great!"
        assert feedback.user_id == "user_1"
        assert feedback.timestamp  # Has timestamp

    def test_add_feedback_thumbs_down(self, store: FeedbackStore) -> None:
        """Add thumbs down feedback."""
        feedback = store.add_feedback(
            thread_id="thread_1",
            message_index=3,
            feedback_type="thumbs_down",
            tags=["inaccurate", "too_verbose"],
            comment="Had errors",
        )

        assert feedback.feedback_type == "thumbs_down"
        assert feedback.tags == ("inaccurate", "too_verbose")

    def test_add_feedback_invalid_type(self, store: FeedbackStore) -> None:
        """Reject invalid feedback type."""
        with pytest.raises(ValueError, match="Invalid feedback_type"):
            store.add_feedback(
                thread_id="thread_1",
                message_index=0,
                feedback_type="invalid",  # type: ignore
            )

    def test_add_feedback_negative_index(self, store: FeedbackStore) -> None:
        """Reject negative message index."""
        with pytest.raises(ValueError, match="non-negative"):
            store.add_feedback(
                thread_id="thread_1",
                message_index=-1,
                feedback_type="thumbs_up",
            )

    def test_add_feedback_no_tags(self, store: FeedbackStore) -> None:
        """Add feedback without tags."""
        feedback = store.add_feedback(
            thread_id="thread_1",
            message_index=0,
            feedback_type="thumbs_up",
        )

        assert feedback.tags == ()
        assert feedback.comment == ""

    def test_add_feedback_strips_whitespace(self, store: FeedbackStore) -> None:
        """Tags and comments are stripped."""
        feedback = store.add_feedback(
            thread_id="thread_1",
            message_index=0,
            feedback_type="thumbs_up",
            tags=["  helpful  ", "  CONFUSING  "],
            comment="  Great work!  ",
        )

        assert feedback.tags == ("helpful", "confusing")
        assert feedback.comment == "Great work!"

    def test_get_feedback_empty(self, store: FeedbackStore) -> None:
        """Get feedback for thread with no feedback."""
        feedbacks = store.get_feedback("nonexistent_thread")
        assert feedbacks == []

    def test_get_feedback_single(self, store: FeedbackStore) -> None:
        """Get feedback after adding one."""
        store.add_feedback(
            thread_id="thread_1",
            message_index=5,
            feedback_type="thumbs_up",
            tags=["helpful"],
        )

        feedbacks = store.get_feedback("thread_1")
        assert len(feedbacks) == 1
        assert feedbacks[0].message_index == 5
        assert feedbacks[0].feedback_type == "thumbs_up"

    def test_get_feedback_multiple(self, store: FeedbackStore) -> None:
        """Get multiple feedbacks in chronological order."""
        store.add_feedback(
            thread_id="thread_1",
            message_index=2,
            feedback_type="thumbs_up",
        )

        store.add_feedback(
            thread_id="thread_1",
            message_index=5,
            feedback_type="thumbs_down",
        )

        store.add_feedback(
            thread_id="thread_1",
            message_index=2,
            feedback_type="thumbs_down",
            tags=["inaccurate"],
        )

        feedbacks = store.get_feedback("thread_1")
        assert len(feedbacks) == 3
        assert feedbacks[0].message_index == 2
        assert feedbacks[0].feedback_type == "thumbs_up"
        assert feedbacks[1].message_index == 5
        assert feedbacks[2].message_index == 2
        assert feedbacks[2].tags == ("inaccurate",)

    def test_get_message_feedback(self, store: FeedbackStore) -> None:
        """Get feedback for specific message."""
        store.add_feedback(
            thread_id="thread_1",
            message_index=2,
            feedback_type="thumbs_up",
        )

        store.add_feedback(
            thread_id="thread_1",
            message_index=5,
            feedback_type="thumbs_down",
        )

        store.add_feedback(
            thread_id="thread_1",
            message_index=2,
            feedback_type="thumbs_down",
        )

        # Get only message 2 feedback
        feedbacks = store.get_message_feedback("thread_1", 2)
        assert len(feedbacks) == 2
        assert all(f.message_index == 2 for f in feedbacks)

        # Get only message 5 feedback
        feedbacks = store.get_message_feedback("thread_1", 5)
        assert len(feedbacks) == 1
        assert feedbacks[0].message_index == 5


class TestFeedbackStats:
    """Test feedback statistics."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> FeedbackStore:
        """Create store with sample feedback."""
        store = FeedbackStore(tmp_path)

        store.add_feedback(
            thread_id="thread_1",
            message_index=2,
            feedback_type="thumbs_up",
            tags=["helpful"],
        )

        store.add_feedback(
            thread_id="thread_1",
            message_index=5,
            feedback_type="thumbs_down",
            tags=["inaccurate", "too_verbose"],
        )

        store.add_feedback(
            thread_id="thread_1",
            message_index=2,
            feedback_type="thumbs_down",
            tags=["confusing"],
        )

        return store

    def test_get_stats(self, store: FeedbackStore) -> None:
        """Get feedback statistics."""
        stats = store.get_stats("thread_1")

        assert stats["total"] == 3
        assert stats["thumbs_up"] == 1
        assert stats["thumbs_down"] == 2
        assert stats["messages_with_feedback"] == [2, 5]

        # Tag counts
        assert stats["tags"]["helpful"] == 1
        assert stats["tags"]["inaccurate"] == 1
        assert stats["tags"]["too_verbose"] == 1
        assert stats["tags"]["confusing"] == 1

    def test_get_stats_empty(self, tmp_path: Path) -> None:
        """Stats for thread with no feedback."""
        store = FeedbackStore(tmp_path)
        stats = store.get_stats("nonexistent")

        assert stats["total"] == 0
        assert stats["thumbs_up"] == 0
        assert stats["thumbs_down"] == 0
        assert stats["tags"] == {}
        assert stats["messages_with_feedback"] == []


class TestRLHFExport:
    """Test RLHF dataset export."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> FeedbackStore:
        """Create store with feedback across multiple threads."""
        store = FeedbackStore(tmp_path)

        # Thread 1: 2 feedbacks
        store.add_feedback(
            thread_id="thread_1",
            message_index=2,
            feedback_type="thumbs_up",
            tags=["helpful"],
        )

        store.add_feedback(
            thread_id="thread_1",
            message_index=5,
            feedback_type="thumbs_down",
            tags=["inaccurate"],
        )

        # Thread 2: 1 feedback
        store.add_feedback(
            thread_id="thread_2",
            message_index=0,
            feedback_type="thumbs_up",
        )

        # Thread 3: 3 feedbacks
        store.add_feedback(
            thread_id="thread_3",
            message_index=1,
            feedback_type="thumbs_up",
        )

        store.add_feedback(
            thread_id="thread_3",
            message_index=2,
            feedback_type="thumbs_up",
        )

        store.add_feedback(
            thread_id="thread_3",
            message_index=3,
            feedback_type="thumbs_down",
        )

        return store

    def test_export_all(self, store: FeedbackStore, tmp_path: Path) -> None:
        """Export all feedback."""
        output = tmp_path / "rlhf_dataset.jsonl"
        count = store.export_rlhf_dataset(output)

        assert count == 6  # All feedbacks exported
        assert output.exists()

        # Verify format
        lines = output.read_text().strip().split("\n")
        assert len(lines) == 6

    def test_export_min_feedback_count(self, store: FeedbackStore, tmp_path: Path) -> None:
        """Export only threads with minimum feedback count."""
        output = tmp_path / "rlhf_dataset.jsonl"
        count = store.export_rlhf_dataset(output, min_feedback_count=2)

        # Thread 1: 2 feedbacks ✓
        # Thread 2: 1 feedback ✗
        # Thread 3: 3 feedbacks ✓
        assert count == 5  # 2 + 3

    def test_export_filter_thumbs_up(self, store: FeedbackStore, tmp_path: Path) -> None:
        """Export only thumbs up feedback."""
        output = tmp_path / "rlhf_dataset.jsonl"
        count = store.export_rlhf_dataset(output, feedback_type_filter="thumbs_up")

        # Thread 1: 1 thumbs_up
        # Thread 2: 1 thumbs_up
        # Thread 3: 2 thumbs_up
        assert count == 4

    def test_export_filter_thumbs_down(self, store: FeedbackStore, tmp_path: Path) -> None:
        """Export only thumbs down feedback."""
        output = tmp_path / "rlhf_dataset.jsonl"
        count = store.export_rlhf_dataset(output, feedback_type_filter="thumbs_down")

        # Thread 1: 1 thumbs_down
        # Thread 3: 1 thumbs_down
        assert count == 2

    def test_export_combined_filters(self, store: FeedbackStore, tmp_path: Path) -> None:
        """Combine min_feedback_count and type filter."""
        output = tmp_path / "rlhf_dataset.jsonl"
        count = store.export_rlhf_dataset(
            output,
            min_feedback_count=2,
            feedback_type_filter="thumbs_up",
        )

        # Thread 1: 2 total, 1 thumbs_up ✓
        # Thread 2: 1 total ✗ (below min)
        # Thread 3: 3 total, 2 thumbs_up ✓
        assert count == 3  # 1 + 2

    def test_export_empty_store(self, tmp_path: Path) -> None:
        """Export from empty store."""
        store = FeedbackStore(tmp_path)
        output = tmp_path / "rlhf_dataset.jsonl"
        count = store.export_rlhf_dataset(output)

        assert count == 0


class TestPersistence:
    """Test feedback persistence across store instances."""

    def test_feedback_persists(self, tmp_path: Path) -> None:
        """Feedback persists when store is closed and reopened."""
        # Create store and add feedback
        store1 = FeedbackStore(tmp_path)
        store1.add_feedback(
            thread_id="thread_1",
            message_index=5,
            feedback_type="thumbs_up",
            tags=["helpful"],
            comment="Great!",
        )

        # Close and reopen
        del store1

        store2 = FeedbackStore(tmp_path)
        feedbacks = store2.get_feedback("thread_1")

        assert len(feedbacks) == 1
        assert feedbacks[0].message_index == 5
        assert feedbacks[0].feedback_type == "thumbs_up"
        assert feedbacks[0].tags == ("helpful",)
        assert feedbacks[0].comment == "Great!"

    def test_append_only(self, tmp_path: Path) -> None:
        """Feedback is append-only across instances."""
        # First instance
        store1 = FeedbackStore(tmp_path)
        store1.add_feedback(
            thread_id="thread_1",
            message_index=2,
            feedback_type="thumbs_up",
        )

        del store1

        # Second instance adds more
        store2 = FeedbackStore(tmp_path)
        store2.add_feedback(
            thread_id="thread_1",
            message_index=5,
            feedback_type="thumbs_down",
        )

        # Both feedbacks should exist
        feedbacks = store2.get_feedback("thread_1")
        assert len(feedbacks) == 2
        assert feedbacks[0].message_index == 2
        assert feedbacks[1].message_index == 5


class TestStandardTags:
    """Test standard feedback tags."""

    def test_standard_tags_defined(self) -> None:
        """Standard tags are defined."""
        assert "helpful" in FEEDBACK_TAGS
        assert "inaccurate" in FEEDBACK_TAGS
        assert "too_verbose" in FEEDBACK_TAGS
        assert "off_topic" in FEEDBACK_TAGS
        assert "harmful" in FEEDBACK_TAGS
        assert "incomplete" in FEEDBACK_TAGS
        assert "confusing" in FEEDBACK_TAGS

    def test_can_use_custom_tags(self, tmp_path: Path) -> None:
        """Custom tags are allowed."""
        store = FeedbackStore(tmp_path)
        feedback = store.add_feedback(
            thread_id="thread_1",
            message_index=0,
            feedback_type="thumbs_up",
            tags=["custom_tag", "another_custom"],
        )

        assert feedback.tags == ("custom_tag", "another_custom")

