"""Low-level append operations for Markdown-backed agent memory."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def append_memory_line(path: Path, *, fact: str, tags: list[str]) -> None:
    """Append one timestamped fact, removing the empty scaffold marker."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if "_No memories yet._" in existing:
            cleaned = (
                "\n".join(
                    line for line in existing.splitlines() if line.strip() != "_No memories yet._"
                ).rstrip()
                + "\n"
            )
            path.write_text(cleaned, encoding="utf-8")

    tag_str = ",".join(str(tag) for tag in tags)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"- [{iso_now()} · {tag_str}] {fact}\n")


__all__ = ["append_memory_line", "iso_now"]
