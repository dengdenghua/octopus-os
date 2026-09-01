"""
Slash-command loader · parse markdown files into ``SlashCommand``
dataclasses · expand templates with user-supplied arguments.

Why Python-side · not a separate config schema: markdown-with-
frontmatter is the de-facto slash-command interchange format.
Community users can drop files in `~/.echo/commands/` and
every agent instance picks them up. Zero coupling to our
internal skill registry.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ═══════════════════════════════════════════════════════════
# Data model
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SlashCommand:
    """A loaded command. Immutable so the registry can be shared
    across threads without copying."""

    name: str
    body: str
    description: str = ""
    argument_hint: str = ""
    allowed_tools: tuple[str, ...] = ()
    model: str = ""
    source: str = ""  # "global" | "project"
    path: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Serializable projection · used by the HTTP list endpoint."""
        return {
            "name": self.name,
            "description": self.description,
            "argument_hint": self.argument_hint,
            "allowed_tools": list(self.allowed_tools),
            "model": self.model,
            "source": self.source,
        }


# ═══════════════════════════════════════════════════════════
# Frontmatter parsing
# ═══════════════════════════════════════════════════════════

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?\n)---\s*\n(.*)\Z",
    re.DOTALL,
)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (metadata, body). Non-frontmatter files: empty metadata,
    whole text as body. Intentionally NOT using PyYAML to avoid a
    runtime dep — frontmatter here is flat `key: value` lines."""
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        return {}, text
    fm_text, body = m.group(1), m.group(2)
    meta: dict[str, str] = {}
    for raw in fm_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip().strip("'\"")
    return meta, body.lstrip("\n")


def _coerce_tool_list(raw: str) -> tuple[str, ...]:
    """Tools are comma-separated in frontmatter · trim and filter
    empties. Accept `a, b, c` or `a,b,c`."""
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


# ═══════════════════════════════════════════════════════════
# Directory discovery
# ═══════════════════════════════════════════════════════════


def _global_commands_dir() -> Path:
    """~/.echo/commands/ · overrideable via $ECHO_HOME."""
    home = os.environ.get("ECHO_HOME")
    if home:
        return Path(home) / "commands"
    return Path.home() / ".echo" / "commands"


def _project_commands_dir(project_dir: Path | str | None) -> Path | None:
    """Project-local <dir>/.echo/commands/ · None if no project dir."""
    if project_dir is None:
        return None
    return Path(project_dir) / ".echo" / "commands"


def _bundled_commands_dir() -> Path | None:
    """Bundled commands shipped with the app (<pkg>/slash_commands/bundled/).

    Lowest-precedence tier: users can shadow a bundled command by dropping a
    same-named file in their global or project commands dir. Returns None if
    the directory is absent (e.g. source-tree layouts without the bundled
    folder) so a missing bundle never crashes loading.
    """
    bundle = Path(__file__).resolve().parent / "bundled"
    return bundle if bundle.is_dir() else None


# ═══════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════


def load_slash_commands(
    project_dir: Path | str | None = None,
) -> list[SlashCommand]:
    """Load + merge commands from bundled → global → project directories.

    Project-level commands override global commands with the same
    name · global overrides bundled · this mirrors how project-level
    directories shadow user-level ones in standard slash-command layouts.
    The bundled tier ships app-built-in commands (e.g. the Project OS
    control surface) so the composer typeahead works out of the box.

    Missing directories are fine (returns empty list from that tier).
    Malformed files are skipped silently · one bad file shouldn't
    lock out the whole catalog. (Callers that want strict validation
    can call ``_load_file_strict`` directly in tests.)
    """
    by_name: dict[str, SlashCommand] = {}

    for source, dirpath in (
        ("bundled", _bundled_commands_dir()),
        ("global", _global_commands_dir()),
        ("project", _project_commands_dir(project_dir)),
    ):
        if dirpath is None:
            continue
        if not dirpath.is_dir():
            continue
        for path in sorted(dirpath.glob("*.md")):
            try:
                cmd = _load_file(path, source)
            except (OSError, ValueError):
                continue
            by_name[cmd.name] = cmd

    return list(by_name.values())


def _load_file(path: Path, source: str) -> SlashCommand:
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    return SlashCommand(
        name=path.stem,
        body=body.rstrip(),
        description=meta.get("description", ""),
        argument_hint=meta.get("argument-hint", ""),
        allowed_tools=_coerce_tool_list(meta.get("allowed-tools", "")),
        model=meta.get("model", ""),
        source=source,
        path=str(path),
    )


# ═══════════════════════════════════════════════════════════
# Template expansion
# ═══════════════════════════════════════════════════════════

_POSITIONAL_RE = re.compile(r"\$(\d+)")
_NAMED_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def expand(
    cmd: SlashCommand,
    args: str | list[str] | dict[str, str] | None = None,
) -> str:
    """Expand a SlashCommand template with user-supplied args.

    ``args`` accepts:

    * ``None`` or ``""`` — only $ARGUMENTS / named placeholders
      resolve (to empty string); $1/$2 stay as-is if no values
    * ``str`` — treated as the raw argument line · shlex-split into
      positional tokens · $ARGUMENTS gets the full string
    * ``list[str]`` — positional tokens directly · $ARGUMENTS gets
      them space-joined
    * ``dict[str, str]`` — named args · $<name> expands · $ARGUMENTS
      unresolved (empty)
    """
    positional: list[str] = []
    raw_arg_string = ""
    named: dict[str, str] = {}

    if isinstance(args, str):
        raw_arg_string = args
        try:
            positional = shlex.split(args) if args.strip() else []
        except ValueError:
            # Unmatched quote · fall back to whitespace split so
            # we never crash on user input.
            positional = args.split()
    elif isinstance(args, list):
        positional = list(args)
        raw_arg_string = " ".join(positional)
    elif isinstance(args, dict):
        named = {str(k): str(v) for k, v in args.items()}

    out = cmd.body

    # $ARGUMENTS → full arg string (most-common template token)
    out = out.replace("$ARGUMENTS", raw_arg_string)

    # $1 $2 $3 ... → positional (1-indexed, Unix-shell convention)
    def _sub_pos(m: re.Match[str]) -> str:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(positional):
            return positional[idx]
        return m.group(0)  # keep unresolved placeholder

    out = _POSITIONAL_RE.sub(_sub_pos, out)

    # $<name> → dict lookup · unresolved names stay verbatim
    if named:

        def _sub_named(m: re.Match[str]) -> str:
            key = m.group(1)
            if key == "ARGUMENTS":  # already handled
                return m.group(0)
            return named.get(key, m.group(0))

        out = _NAMED_RE.sub(_sub_named, out)

    return out


__all__ = [
    "SlashCommand",
    "load_slash_commands",
    "expand",
    "_load_file",
    "_parse_frontmatter",
]
