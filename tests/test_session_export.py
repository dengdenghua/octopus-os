"""Tests for session export to markdown.

Echo Native session-export tests.
"""

from __future__ import annotations

from runtime.memory.threads.session_export import export_thread_to_markdown


class TestExportBasic:
    """Test basic export functionality."""

    def test_export_simple_conversation(self) -> None:
        """Export a simple back-and-forth conversation."""
        messages = [
            {
                "role": "user",
                "content": "Hello, can you help me?",
                "timestamp": "2026-08-14T10:00:00Z",
            },
            {
                "role": "assistant",
                "content": "Of course! What do you need help with?",
                "timestamp": "2026-08-14T10:00:05Z",
            },
        ]

        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="Simple Conversation",
            messages=messages,
            created_at="2026-08-14T10:00:00Z",
        )

        assert "thread_id: thread_1" in result
        assert "title: Simple Conversation" in result
        assert "# Simple Conversation" in result
        assert "## Message 1: User" in result
        assert "Hello, can you help me?" in result
        assert "## Message 2: Assistant" in result
        assert "Of course!" in result

    def test_export_with_metadata(self) -> None:
        """Export includes agent and team metadata."""
        messages = [{"role": "user", "content": "Test"}]

        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="Test",
            messages=messages,
            agent_id="agent_a",
            team_id="team_1",
            created_at="2026-08-14T10:00:00Z",
            updated_at="2026-08-14T11:00:00Z",
        )

        assert "agent_id: agent_a" in result
        assert "team_id: team_1" in result
        assert "created_at: 2026-08-14T10:00:00Z" in result
        assert "updated_at: 2026-08-14T11:00:00Z" in result

    def test_export_empty_messages(self) -> None:
        """Export thread with no messages."""
        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="Empty Thread",
            messages=[],
        )

        assert "# Empty Thread" in result
        assert "thread_id: thread_1" in result

    def test_export_timestamps_in_headers(self) -> None:
        """Timestamps appear in message headers."""
        messages = [
            {
                "role": "user",
                "content": "Test",
                "timestamp": "2026-08-14T10:30:00Z",
            }
        ]

        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="Test",
            messages=messages,
        )

        assert "## Message 1: User (2026-08-14T10:30:00Z)" in result


class TestExportMultipart:
    """Test export of multipart content."""

    def test_export_tool_call(self) -> None:
        """Export message with tool call."""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me search for that."},
                    {
                        "type": "tool_use",
                        "name": "search_files",
                        "input": {"query": "authentication", "path": "src/"},
                    },
                ],
            }
        ]

        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="Tool Use",
            messages=messages,
        )

        assert "Let me search for that." in result
        assert "**Tool Call:** `search_files`" in result
        assert "```json" in result
        assert '"query": "authentication"' in result
        assert '"path": "src/"' in result

    def test_export_tool_result(self) -> None:
        """Export message with tool result."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_123",
                        "content": "Found 3 matches:\n- auth.py\n- login.py\n- token.py",
                    }
                ],
            }
        ]

        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="Tool Result",
            messages=messages,
        )

        assert "**Tool Result:** (call_id: call_123)" in result
        assert "Found 3 matches:" in result
        assert "- auth.py" in result

    def test_export_tool_result_json(self) -> None:
        """Export tool result that looks like JSON."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_123",
                        "content": '{"status": "success", "count": 3}',
                    }
                ],
            }
        ]

        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="JSON Result",
            messages=messages,
        )

        assert "```json" in result
        assert '{"status": "success", "count": 3}' in result

    def test_export_image(self) -> None:
        """Export message with image."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this:"},
                    {"type": "image", "url": "https://example.com/image.png"},
                ],
            }
        ]

        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="Image",
            messages=messages,
        )

        assert "Look at this:" in result
        assert "![Image](https://example.com/image.png)" in result

    def test_export_mixed_content(self) -> None:
        """Export message with multiple content types."""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Here's what I found:"},
                    {
                        "type": "tool_use",
                        "name": "read_file",
                        "input": {"path": "config.yaml"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_456",
                        "content": "port: 8000\nhost: localhost",
                    }
                ],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "The server runs on port 8000."}],
            },
        ]

        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="Mixed Content",
            messages=messages,
        )

        assert "Here's what I found:" in result
        assert "**Tool Call:** `read_file`" in result
        assert "port: 8000" in result
        assert "The server runs on port 8000." in result


class TestExportEdgeCases:
    """Test edge cases in export."""

    def test_export_missing_role(self) -> None:
        """Handle message without role."""
        messages = [{"content": "Hello"}]

        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="Test",
            messages=messages,
        )

        assert "## Message 1: Unknown" in result
        assert "Hello" in result

    def test_export_missing_content(self) -> None:
        """Handle message without content."""
        messages = [{"role": "user"}]

        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="Test",
            messages=messages,
        )

        # Should not crash, just empty content section
        assert "## Message 1: User" in result

    def test_export_non_string_content(self) -> None:
        """Handle non-string content gracefully."""
        messages = [
            {"role": "user", "content": 123},
            {"role": "assistant", "content": None},
            {"role": "user", "content": {"key": "value"}},
        ]

        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="Test",
            messages=messages,
        )

        # Should convert to string
        assert "123" in result
        assert "None" in result
        assert "{'key': 'value'}" in result

    def test_export_unknown_content_type(self) -> None:
        """Handle unknown content part types."""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Known type"},
                    {"type": "unknown_future_type", "data": "something"},
                ],
            }
        ]

        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="Test",
            messages=messages,
        )

        assert "Known type" in result
        assert "[unknown_future_type]" in result

    def test_export_markdown_in_content(self) -> None:
        """Preserve markdown formatting in content."""
        messages = [
            {
                "role": "user",
                "content": "# This is a heading\n\n**Bold text** and `code`",
            }
        ]

        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="Markdown Content",
            messages=messages,
        )

        assert "# This is a heading" in result
        assert "**Bold text**" in result
        assert "`code`" in result

    def test_export_code_blocks_preserved(self) -> None:
        """Code blocks in content are preserved."""
        messages = [
            {
                "role": "assistant",
                "content": "Here's the code:\n\n```python\ndef hello():\n    print('world')\n```",
            }
        ]

        result = export_thread_to_markdown(
            thread_id="thread_1",
            title="Code Block",
            messages=messages,
        )

        assert "```python" in result
        assert "def hello():" in result
        assert "print('world')" in result

    def test_export_special_yaml_characters(self) -> None:
        """Handle special characters in YAML frontmatter."""
        messages = [{"role": "user", "content": "Test"}]

        result = export_thread_to_markdown(
            thread_id="thread:123",
            title="Title with: colons",
            messages=messages,
        )

        # Should still be valid YAML-ish
        assert "thread_id: thread:123" in result
        assert "title: Title with: colons" in result

