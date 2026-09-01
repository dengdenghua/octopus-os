"""Message feedback collection system.

Echo Native feedback collection for assistant-message evaluation data.

Architecture:
- Append-only JSONL storage per thread
- Feedback types: thumbs_up, thumbs_down with optional tags
- Tags: "inaccurate", "too_verbose", "helpful", "off_topic", "harmful"
- Immutable once written (no updates/deletes)
- Thread-safe with file locking

Schema:
{
    "thread_id": "abc123",
    "message_index": 5,
    "feedback_type": "thumbs_down",
    "tags": ["inaccurate", "too_verbose"],
    "comment": "The code example had a bug",
    "timestamp": "2026-08-14T10:30:00Z",
    "user_id": "user_123"
}
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

FeedbackType = Literal["thumbs_up", "thumbs_down"]

# Standard feedback tags
FEEDBACK_TAGS = {
    "helpful",
    "inaccurate",
    "too_verbose",
    "off_topic",
    "harmful",
    "incomplete",
    "confusing",
}


@dataclass(frozen=True)
class MessageFeedback:
    """User feedback on a single message."""

    thread_id: str
    message_index: int
    feedback_type: FeedbackType
    tags: tuple[str, ...]
    comment: str
    timestamp: str
    user_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "thread_id": self.thread_id,
            "message_index": self.message_index,
            "feedback_type": self.feedback_type,
            "tags": list(self.tags),
            "comment": self.comment,
            "timestamp": self.timestamp,
            "user_id": self.user_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessageFeedback:
        """Construct from dictionary."""
        return cls(
            thread_id=data["thread_id"],
            message_index=data["message_index"],
            feedback_type=data["feedback_type"],
            tags=tuple(data.get("tags", [])),
            comment=data.get("comment", ""),
            timestamp=data["timestamp"],
            user_id=data.get("user_id"),
        )


class FeedbackStore:
    """Thread-safe append-only feedback storage.

    Stores feedback in per-thread JSONL files:
    - <base>/feedback/<thread_id>.jsonl

    Each line is one feedback entry. Append-only semantics ensure
    immutability for RLHF data integrity.
    """

    def __init__(self, base_path: str | Path) -> None:
        self._base_path = Path(base_path)
        self._lock = threading.RLock()

    def _feedback_file(self, thread_id: str) -> Path:
        """Return path to feedback file for a thread."""
        return self._base_path / "feedback" / f"{thread_id}.jsonl"

    def add_feedback(
        self,
        thread_id: str,
        message_index: int,
        feedback_type: FeedbackType,
        *,
        tags: list[str] | None = None,
        comment: str = "",
        user_id: str | None = None,
    ) -> MessageFeedback:
        """Add feedback for a message.

        Args:
            thread_id: Thread identifier
            message_index: Zero-based index of the message
            feedback_type: "thumbs_up" or "thumbs_down"
            tags: Optional list of tags (e.g., ["helpful"], ["inaccurate", "too_verbose"])
            comment: Optional free-form comment
            user_id: Optional user identifier

        Returns:
            The recorded MessageFeedback

        Raises:
            ValueError: If feedback_type is invalid or message_index is negative
        """
        if feedback_type not in ("thumbs_up", "thumbs_down"):
            raise ValueError(f"Invalid feedback_type: {feedback_type}")

        if message_index < 0:
            raise ValueError(f"message_index must be non-negative, got {message_index}")

        # Validate tags
        validated_tags = []
        if tags:
            for tag in tags:
                tag_clean = tag.strip().lower()
                if tag_clean:
                    validated_tags.append(tag_clean)

        timestamp = datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"

        feedback = MessageFeedback(
            thread_id=thread_id,
            message_index=message_index,
            feedback_type=feedback_type,
            tags=tuple(validated_tags),
            comment=comment.strip(),
            timestamp=timestamp,
            user_id=user_id,
        )

        with self._lock:
            feedback_file = self._feedback_file(thread_id)
            feedback_file.parent.mkdir(parents=True, exist_ok=True)

            # Append to file
            with open(feedback_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(feedback.to_dict()) + "\n")

        return feedback

    def get_feedback(self, thread_id: str) -> list[MessageFeedback]:
        """Get all feedback for a thread.

        Args:
            thread_id: Thread identifier

        Returns:
            List of MessageFeedback, in chronological order
        """
        feedback_file = self._feedback_file(thread_id)

        if not feedback_file.exists():
            return []

        feedbacks = []
        with self._lock:
            try:
                with open(feedback_file, encoding="utf-8") as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            data = json.loads(line)
                            feedback = MessageFeedback.from_dict(data)
                            feedbacks.append(feedback)
                        except (json.JSONDecodeError, KeyError, TypeError) as e:
                            logger.warning(
                                "Skipping malformed feedback entry",
                                extra={
                                    "thread_id": thread_id,
                                    "line_num": line_num,
                                    "error": str(e),
                                },
                            )
            except OSError as e:
                logger.warning(
                    "Failed to read feedback file",
                    extra={"thread_id": thread_id, "error": str(e)},
                )

        return feedbacks

    def get_message_feedback(self, thread_id: str, message_index: int) -> list[MessageFeedback]:
        """Get all feedback for a specific message.

        Args:
            thread_id: Thread identifier
            message_index: Zero-based message index

        Returns:
            List of MessageFeedback for this message, in chronological order
        """
        all_feedback = self.get_feedback(thread_id)
        return [f for f in all_feedback if f.message_index == message_index]

    def get_stats(self, thread_id: str) -> dict[str, Any]:
        """Get feedback statistics for a thread.

        Returns:
            Dictionary with counts:
            - total: Total feedback count
            - thumbs_up: Positive feedback count
            - thumbs_down: Negative feedback count
            - tags: Dict of tag -> count
            - messages_with_feedback: Set of message indices with any feedback
        """
        feedbacks = self.get_feedback(thread_id)

        thumbs_up = sum(1 for f in feedbacks if f.feedback_type == "thumbs_up")
        thumbs_down = sum(1 for f in feedbacks if f.feedback_type == "thumbs_down")

        tag_counts: dict[str, int] = {}
        messages_with_feedback = set()

        for feedback in feedbacks:
            messages_with_feedback.add(feedback.message_index)
            for tag in feedback.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        return {
            "total": len(feedbacks),
            "thumbs_up": thumbs_up,
            "thumbs_down": thumbs_down,
            "tags": tag_counts,
            "messages_with_feedback": sorted(messages_with_feedback),
        }

    def export_rlhf_dataset(
        self,
        output_path: str | Path,
        *,
        min_feedback_count: int = 1,
        feedback_type_filter: FeedbackType | None = None,
    ) -> int:
        """Export all feedback as RLHF training dataset.

        Args:
            output_path: Path to write JSONL dataset
            min_feedback_count: Only include threads with at least this many feedbacks
            feedback_type_filter: Only include specific feedback type (optional)

        Returns:
            Number of feedback entries exported
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        exported_count = 0

        with self._lock:
            feedback_dir = self._base_path / "feedback"
            if not feedback_dir.exists():
                return 0

            with open(output_path, "w", encoding="utf-8") as out_f:
                for feedback_file in sorted(feedback_dir.glob("*.jsonl")):
                    thread_id = feedback_file.stem
                    feedbacks = self.get_feedback(thread_id)

                    if len(feedbacks) < min_feedback_count:
                        continue

                    for feedback in feedbacks:
                        if (
                            feedback_type_filter is not None
                            and feedback.feedback_type != feedback_type_filter
                        ):
                            continue

                        out_f.write(json.dumps(feedback.to_dict()) + "\n")
                        exported_count += 1

        return exported_count


__all__ = [
    "FeedbackType",
    "MessageFeedback",
    "FeedbackStore",
    "FEEDBACK_TAGS",
]
