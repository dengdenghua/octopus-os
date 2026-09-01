"""Session export to Markdown.

Echo Native Session API v2 export for thread transcripts.

Export format:
- Frontmatter with metadata (YAML)
- Each message as section with role and timestamp
- Tool calls and observations rendered clearly
- Code blocks preserved
"""

from __future__ import annotations

from typing import Any


def export_thread_to_markdown(
    thread_id: str,
    title: str,
    messages: list[dict[str, Any]],
    *,
    agent_id: str | None = None,
    team_id: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> str:
    """Export a thread to Markdown format.

    Args:
        thread_id: Thread identifier
        title: Thread title
        messages: List of message dicts with role, content, timestamp
        agent_id: Optional agent identifier
        team_id: Optional team identifier
        created_at: ISO timestamp
        updated_at: ISO timestamp

    Returns:
        Markdown string with YAML frontmatter
    """
    lines = []

    # YAML frontmatter
    lines.append("---")
    lines.append(f"thread_id: {thread_id}")
    lines.append(f"title: {title}")
    if agent_id:
        lines.append(f"agent_id: {agent_id}")
    if team_id:
        lines.append(f"team_id: {team_id}")
    if created_at:
        lines.append(f"created_at: {created_at}")
    if updated_at:
        lines.append(f"updated_at: {updated_at}")
    lines.append("---")
    lines.append("")

    # Title
    lines.append(f"# {title}")
    lines.append("")

    # Messages
    for i, msg in enumerate(messages, 1):
        role = msg.get("role", "unknown")
        timestamp = msg.get("timestamp", "")
        content = msg.get("content", "")

        # Message header
        header = f"## Message {i}: {role.title()}"
        if timestamp:
            header += f" ({timestamp})"
        lines.append(header)
        lines.append("")

        # Content
        if isinstance(content, str):
            lines.append(content)
        elif isinstance(content, list):
            # Multipart content
            for part in content:
                if isinstance(part, dict):
                    part_type = part.get("type", "")
                    if part_type == "text":
                        text = part.get("text", "")
                        lines.append(str(text))
                    elif part_type == "tool_use":
                        tool_name = part.get("name", "unknown")
                        tool_input = part.get("input", {})
                        lines.append(f"**Tool Call:** `{tool_name}`")
                        lines.append("")
                        lines.append("```json")
                        import json

                        lines.append(json.dumps(tool_input, indent=2))
                        lines.append("```")
                    elif part_type == "tool_result":
                        tool_id = part.get("tool_use_id", "unknown")
                        result = part.get("content", "")
                        lines.append(f"**Tool Result:** (call_id: {tool_id})")
                        lines.append("")
                        if isinstance(result, str):
                            # Try to detect if it's JSON or code
                            if result.strip().startswith(("{", "[")):
                                lines.append("```json")
                                lines.append(result)
                                lines.append("```")
                            else:
                                lines.append("```")
                                lines.append(result)
                                lines.append("```")
                        else:
                            lines.append(str(result))
                    elif part_type == "image":
                        url = part.get("url", "")
                        lines.append(f"![Image]({url})")
                    else:
                        lines.append(f"[{part_type}]")
                else:
                    lines.append(str(part))
        else:
            lines.append(str(content))

        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


__all__ = ["export_thread_to_markdown"]
