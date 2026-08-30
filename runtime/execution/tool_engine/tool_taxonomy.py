"""Unified tool identity layer · stable taxonomy for audit & grouping.

Inspired by Grok Build's ``tool_taxonomy.rs``: every tool call carries
stable identity metadata (``kind`` / ``namespace`` / ``readonly`` /
``version``) so that the TUI, ACP-style IDE clients, and audit logs can
group/filter/permission tools without re-inferring from heterogeneous
``affinity`` tags and ``trusted_source`` URIs.

This module is **additive** — it does not modify the existing ``Skill``
dataclass or ``SkillRegistry``. Instead, it derives a ``ToolTaxonomy``
from the existing fields (``affinity`` + ``trusted_source`` + ``name``)
plus optional explicit overrides registered via ``register_taxonomy``.

Design rules
------------
* ``kind`` ∈ {Read, Edit, Execute, WebSearch, Subagent, MCP, Other}
  — mirrors Grok Build's stable semantic kinds.
* ``namespace`` is derived from ``trusted_source`` URI scheme
  (``builtin://`` → ``builtin``, ``skill://public/`` → ``skill.public``,
  ``mcp://`` → ``mcp``). Custom sources keep their scheme verbatim.
* ``readonly`` is True iff ``kind == Read`` OR ``affinity`` contains
  none of ``write/edit/exec/delete/dangerous``.
* ``version`` defaults to ``1``; bump via ``register_taxonomy``.

Usage
-----
::

    from runtime.execution.tool_engine.tool_taxonomy import (
        classify_skill,
        taxonomy_to_audit_dict,
    )

    skill = registry.get("read_file")
    tax = classify_skill(skill)
    audit = taxonomy_to_audit_dict(tax)
    journal.write(ToolLifecycleEvent(..., meta={"x.echo/tool": audit}))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ── Stable semantic kinds ────────────────────────────────────
# Kept intentionally small and aligned with Grok Build's taxonomy
# so downstream consumers (TUI grouping, ACP rendering, permission
# audits) share one vocabulary.
ToolKind = Literal[
    "Read",  # pure read: read_file / search / list
    "Edit",  # write/edit/create/delete files
    "Execute",  # shell / subprocess / arm execution
    "WebSearch",  # http / crawler / browser_read
    "Subagent",  # spawn_subagent / cowork dispatch
    "MCP",  # MCP server tools
    "Other",  # everything else (notebook, git, etc.)
]

# affinity tags that imply a side-effecting tool.
_SIDE_EFFECT_AFFINITIES = frozenset(
    {
        "write",
        "edit",
        "exec",
        "delete",
        "dangerous",
    }
)


@dataclass(frozen=True)
class ToolTaxonomy:
    """Stable identity metadata for a single tool invocation.

    All fields are derived from ``Skill`` via :func:`classify_skill`
    unless the registry has an explicit override (see
    :func:`register_taxonomy`).
    """

    kind: ToolKind
    namespace: str  # "builtin" / "skill.public" / "mcp" / custom
    readonly: bool
    version: int = 1
    # Free-form tags passed through from ``Skill.affinity`` for
    # callers that still want the raw hint set (e.g. ``dangerous``).
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "namespace": self.namespace,
            "readonly": self.readonly,
            "version": self.version,
            "tags": list(self.tags),
        }


# ── Explicit overrides ───────────────────────────────────────
# A small registry of per-skill overrides for cases where the
# derived kind/namespace is wrong (e.g. a ``read_file`` skill that
# also has the ``file`` affinity shouldn't be classified as Edit).
# Overrides are process-local; tests can call ``reset_overrides()``
# to start clean.
_overrides: dict[str, ToolTaxonomy] = {}


def register_taxonomy(skill_name: str, taxonomy: ToolTaxonomy) -> None:
    """Register an explicit taxonomy override for ``skill_name``.

    Subsequent calls to :func:`classify_skill` for that name return
    the override verbatim. Useful for MCP tools and forged composites
    where the auto-derived kind would be wrong.
    """
    _overrides[skill_name] = taxonomy


def reset_overrides() -> None:
    """Clear all registered overrides. Mainly for tests."""
    _overrides.clear()


# ── Classification helpers ───────────────────────────────────


def _derive_namespace(trusted_source: str) -> str:
    """Map ``trusted_source`` URI to a stable namespace.

    ``builtin://fs/read_file``       → ``builtin``
    ``skill://public/dcf``           → ``skill.public``
    ``mcp://filesystem/read``        → ``mcp``
    ``forged://composite_x``         → ``forged``
    ``custom://my-tool``             → ``custom``
    """
    if not trusted_source:
        return "unknown"
    scheme = trusted_source.split("://", 1)[0]
    if scheme == "builtin":
        return "builtin"
    if scheme == "skill":
        # skill://public/x or skill://team/y → skill.public / skill.team
        rest = trusted_source.split("://", 1)[1]
        scope = rest.split("/", 1)[0] or "public"
        return f"skill.{scope}"
    if scheme == "mcp":
        return "mcp"
    return scheme or "unknown"


def _derive_kind(
    skill_name: str,
    affinity: list[str],
    namespace: str,
) -> ToolKind:
    """Pick the most specific ``ToolKind`` from affinity tags + name."""
    # Name-based hints first — they're the most intentional signal.
    name_lower = skill_name.lower()
    if namespace == "mcp":
        return "MCP"
    if name_lower.startswith("spawn_subagent") or name_lower.startswith("cowork_"):
        return "Subagent"
    if "subagent" in affinity or "spawn" in affinity:
        return "Subagent"
    if any(tag in affinity for tag in ("exec", "shell")):
        return "Execute"
    if any(tag in affinity for tag in ("delete",)):
        return "Edit"
    if any(tag in affinity for tag in ("write", "edit")):
        return "Edit"
    if any(tag in affinity for tag in ("web", "crawler", "browser_read")):
        return "WebSearch"
    if "browser_interact" in affinity:
        return "Execute"
    if (
        "file" in affinity
        or name_lower.startswith("read_")
        or name_lower.startswith("list_")
        or name_lower.startswith("search_")
    ):
        return "Read"
    return "Other"


def _derive_readonly(kind: ToolKind, affinity: list[str]) -> bool:
    if kind == "Read" or kind == "WebSearch":
        return True
    # Even an Edit/Execute kind could be tagged readonly=False explicitly,
    # so the kind alone isn't authoritative — but for the common case
    # the absence of any side-effect affinity is the right signal.
    return not any(tag in _SIDE_EFFECT_AFFINITIES for tag in affinity)


def classify_skill(skill: Any) -> ToolTaxonomy:
    """Derive a :class:`ToolTaxonomy` from a ``Skill`` instance.

    Accepts any object with ``name``, ``affinity`` (list[str]), and
    ``trusted_source`` (str) attributes — duck-typed so tests can pass
    a lightweight stand-in without constructing a full ``Skill``.
    """
    name = str(getattr(skill, "name", "") or "")
    if name in _overrides:
        return _overrides[name]

    affinity = list(getattr(skill, "affinity", []) or [])
    trusted_source = str(getattr(skill, "trusted_source", "") or "")
    namespace = _derive_namespace(trusted_source)
    kind = _derive_kind(name, affinity, namespace)
    readonly = _derive_readonly(kind, affinity)
    return ToolTaxonomy(
        kind=kind,
        namespace=namespace,
        readonly=readonly,
        version=1,
        tags=tuple(affinity),
    )


def taxonomy_to_audit_dict(taxonomy: ToolTaxonomy) -> dict[str, Any]:
    """Serialize taxonomy for journal/audit payloads.

    The output shape mirrors Grok Build's ``_meta.x.ai/tool`` envelope
    so downstream consumers (TUI grouping, ACP rendering, permission
    audits) read a uniform ``x.echo/tool`` block.
    """
    return {
        "x.echo/tool": {
            "kind": taxonomy.kind,
            "namespace": taxonomy.namespace,
            "readonly": taxonomy.readonly,
            "version": taxonomy.version,
        }
    }


__all__ = [
    "ToolKind",
    "ToolTaxonomy",
    "classify_skill",
    "taxonomy_to_audit_dict",
    "register_taxonomy",
    "reset_overrides",
]
