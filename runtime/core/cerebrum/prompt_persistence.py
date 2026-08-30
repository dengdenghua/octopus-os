from __future__ import annotations

from datetime import datetime
from pathlib import Path

_HEADER_PREFIX = "# "


def dump_section(path: Path | str, section: str, *, label: str = "") -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    header = (
        f"{_HEADER_PREFIX}echo-agent · {label or 'prompt section'}\n"
        f"{_HEADER_PREFIX}written_at: {now}\n"
        f"{_HEADER_PREFIX}chars: {len(section)}\n"
        f"{_HEADER_PREFIX}---\n"
    )
    p.write_text(header + section, encoding="utf-8")


def load_section(path: Path | str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and lines[i].startswith(_HEADER_PREFIX):
        i += 1
    return "".join(lines[i:])
