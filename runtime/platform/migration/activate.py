"""Activate a staged migration into echo's live stores (the "B" step).

``apply_plan`` already makes **skills** live (registered search-only). This
activates the two surfaces apply deliberately left staged:

* **memory** → appends bounded, idempotent index entries into the project's
  ``.echo/MEMORY.md`` (the project-scope memory echo's recall reads). The
  full imported text stays under ``.echo/imported/<source>/memory/``.
* **mcp** → writes a ready-to-paste ``config.snippet.yaml`` per source. It never
  mutates ``config.yaml`` and never enables a server: ``MCPServerConfigEntry``
  has no ``enabled`` field, so adding one to ``config.yaml`` auto-launches it —
  the user pastes, supplies credentials, and enables explicitly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .base import read_skill_meta

_MARK = "echo-imported"


@dataclass(frozen=True)
class ActivateReport:
    memory_added: int = 0
    memory_skipped: int = 0
    mcp_snippets: dict[str, str] = field(default_factory=dict)


def _imported_root(project_root: Path) -> Path:
    return Path(project_root) / ".echo" / "imported"


def activate_memory(project_root: Path, *, sources: set[str] | None = None) -> tuple[int, int]:
    """Append bounded index entries for staged memory into project MEMORY.md."""
    root = _imported_root(project_root)
    if not root.is_dir():
        return (0, 0)
    index = Path(project_root) / ".echo" / "MEMORY.md"
    existing = index.read_text(encoding="utf-8") if index.is_file() else ""
    new_lines: list[str] = []
    skipped = 0
    for source_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if sources is not None and source_dir.name not in sources:
            continue
        mem_dir = source_dir / "memory"
        if not mem_dir.is_dir():
            continue
        for md in sorted(mem_dir.glob("*.md")):
            rel = md.relative_to(project_root).as_posix()
            if rel in existing:  # idempotent — already indexed
                skipped += 1
                continue
            name, desc = read_skill_meta(md)
            desc = (desc or "").strip() or "(imported memory)"
            new_lines.append(
                f"- [{source_dir.name}] {name}: {desc} (full: {rel}) <!-- {_MARK} -->",
            )
    if new_lines:
        index.parent.mkdir(parents=True, exist_ok=True)
        prefix = "" if not existing else ("" if existing.endswith("\n") else "\n")
        head = "# Imported memory\n\n" if not existing else ""
        with index.open("a", encoding="utf-8") as fh:
            fh.write(prefix + head + "\n".join(new_lines) + "\n")
    return (len(new_lines), skipped)


def _render_mcp_snippet(servers: list[dict]) -> str:
    lines = [
        "# Imported MCP servers — DISABLED by default.",
        "# Review each, copy the full server definition from its `origin`, add",
        "# credentials, then paste under `mcp_servers:` in your config.yaml.",
        "mcp_servers:",
    ]
    for spec in servers:
        lines.append(f"  - name: {spec.get('name', '')}")
        needs = spec.get("needs") or []
        if needs:
            lines.append(f"    # needs: {', '.join(str(n) for n in needs)}")
        if spec.get("origin"):
            lines.append(f"    # full definition in: {spec['origin']}")
    return "\n".join(lines) + "\n"


def activate_mcp_snippet(project_root: Path, *, sources: set[str] | None = None) -> dict[str, str]:
    """Write a paste-ready config snippet per source; never touch config.yaml."""
    root = _imported_root(project_root)
    if not root.is_dir():
        return {}
    out: dict[str, str] = {}
    for source_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if sources is not None and source_dir.name not in sources:
            continue
        disabled = source_dir / "mcp.disabled.json"
        if not disabled.is_file():
            continue
        try:
            data = json.loads(disabled.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        servers = data.get("mcp_servers") or []
        if not servers:
            continue
        snippet_path = source_dir / "config.snippet.yaml"
        snippet_path.write_text(_render_mcp_snippet(servers), encoding="utf-8")
        out[source_dir.name] = str(snippet_path)
    return out


def activate_plan(
    project_root: Path,
    *,
    sources: set[str] | None = None,
    memory: bool = True,
    mcp: bool = True,
) -> ActivateReport:
    added = skipped = 0
    if memory:
        added, skipped = activate_memory(project_root, sources=sources)
    snippets = activate_mcp_snippet(project_root, sources=sources) if mcp else {}
    return ActivateReport(memory_added=added, memory_skipped=skipped, mcp_snippets=snippets)
