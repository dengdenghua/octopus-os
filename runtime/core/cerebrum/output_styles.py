"""Per-turn output style overlays for the ReAct system prompt.

The overlay is an additive paragraph appended at the *end* of the system
prompt (after ``<final-answer-shape>`` and ``<tool-choice-policy>``). It
only changes the closing instruction — same model, same tools, same
behavior — so absent / unknown / "default" styles render to ``""``
(no-op, byte-identical prompt).

Style vocabulary is intentionally tiny so callers can pass it as a flat
string without growing the wire schema:

- ``concise``  — short replies, ≤ 4 sentences, no preamble
- ``detailed`` — explain reasoning, trade-offs, edge cases
- ``audit``    — table / numbered list, severity + one-line fix
- ``review``   — critique-mode, file:line bullets, end with verdict
- ``default`` / ``None`` / unknown → ``""``
"""

from __future__ import annotations

# Each block is ~150 chars, single-paragraph — appended verbatim.
_STYLES: dict[str, str] = {
    "concise": (
        "\n<output-style>\n"
        "Output style: concise. Replies <= 4 sentences unless tool output "
        "requires more. Skip preamble. List only the most relevant 2-3 items.\n"
        "</output-style>"
    ),
    "detailed": (
        "\n<output-style>\n"
        "Output style: detailed. Explain reasoning, trade-offs, and edge "
        "cases. Include code samples and caveats. Default to thorough rather "
        "than terse.\n"
        "</output-style>"
    ),
    "audit": (
        "\n<output-style>\n"
        "Output style: audit. Use a table or numbered list. Mark each finding "
        "with severity (high/medium/low) and a one-line fix recommendation.\n"
        "</output-style>"
    ),
    "review": (
        "\n<output-style>\n"
        "Output style: review. Treat the user's content as a draft to "
        "critique. Bullet specific issues with file:line references. End with "
        "a verdict (approve / changes-requested / blocked).\n"
        "</output-style>"
    ),
}


def render_output_style(style: str | None) -> str:
    """Return the style-overlay block, or ``""`` for None / default / unknown.

    The returned string starts with a leading newline so it can be
    appended directly to ``system_parts`` (which joins with ``""``).
    """
    if not style:
        return ""
    key = style.strip().lower()
    if key in {"", "default"}:
        return ""
    return _STYLES.get(key, "")


__all__ = ["render_output_style"]
