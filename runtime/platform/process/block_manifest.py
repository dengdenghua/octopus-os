"""Block manifest — the declarative "identity + coupler" of one composition block.

Design doc: ``docs/architecture/blocks.md`` (composition layer, §3.1).

A block declares what services it *provides*, what it *consumes*, the
capabilities and sandbox tier it needs, and the events it emits/subscribes
to. The ServiceBus uses this manifest to validate, order, and wire blocks —
so a new block is "a directory + a manifest", not a patch to the core loop.

This module deliberately reuses the vocabulary already present in
``runtime/platform/plugins/plugin_loader.PluginManifest``
(``provides`` / ``subscribes``) and extends it with the fields the
composition layer needs (``consumes``, ``emits``, ``capabilities``,
``sandbox``, ``kind``, ``frontend``). ``from_plugin_manifest`` maps an
existing plugin manifest onto a ``BlockManifest`` so legacy plugins load
unchanged.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

_BLOCK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

# Wire-protocol version of the block manifest shape (P4 · protocol versioning).
# Bump on incompatible field changes; a runtime must reject manifests from a
# NEWER schema it cannot parse instead of misreading them. Mirrors the
# journal's ``CURRENT_SCHEMA_VERSION`` pattern.
BLOCK_MANIFEST_SCHEMA_VERSION = 1


class BlockKind(StrEnum):
    """One of the seven block types from the design taxonomy (§2)."""

    MEMORY = "memory"
    ARM = "arm"
    AGENT = "agent"
    SKILL_PACK = "skill_pack"
    CHANNEL = "channel"
    WIDGET = "widget"
    MODEL_ROUTER = "model_router"
    # Generic fallback for existing plugins that predate the taxonomy.
    PLUGIN = "plugin"


class SandboxMode(StrEnum):
    """Sandbox tier, aligned with the existing three-tier permission model."""

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    FULL_ACCESS = "full_access"


class ApprovalMode(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"
    NEVER = "never"


class SandboxSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: SandboxMode = SandboxMode.WORKSPACE_WRITE
    approval: ApprovalMode = ApprovalMode.AUTO


class DependencySpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    optional: bool = False


class BlockManifest(BaseModel):
    """Validated ``block.yaml``/``plugin.yaml`` shape for one block.

    Validation rules enforced here (and therefore reusable anywhere):
      * ``name`` is a lowercase dotted/underscored id (stable service key).
      * ``kind`` is one of :class:`BlockKind`.
      * a block must not consume a service it provides itself (degenerate
        self-loop), and must not list a service twice in the same list.
      * ``sandbox.mode`` / ``sandbox.approval`` are constrained enums.
    Dependency *resolution* (missing provider vs cycle) is the ServiceBus's
    job — see ``service_bus.resolve_load_order``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128)
    schema_version: int = Field(default=BLOCK_MANIFEST_SCHEMA_VERSION, ge=1)
    version: str = "0.1.0"
    kind: BlockKind = BlockKind.PLUGIN
    description: str = ""
    author: str = ""

    # Service couplers (composition layer, §3.1).
    provides: list[str] = Field(default_factory=list)
    consumes: list[str] = Field(default_factory=list)

    # Event couplers (§3.4) — names follow ``<domain>.<verb>.<participle>``.
    emits: list[str] = Field(default_factory=list)
    subscribes: list[str] = Field(default_factory=list)

    # Runtime requirements.
    capabilities: list[str] = Field(default_factory=list)
    dependencies: list[DependencySpec] = Field(default_factory=list)
    sandbox: SandboxSpec = Field(default_factory=SandboxSpec)

    # Widget-only: where the panel registers in the workbench.
    frontend: dict[str, Any] | None = None

    @field_validator("schema_version")
    @classmethod
    def _schema_must_be_supported(cls, value: int) -> int:
        if value > BLOCK_MANIFEST_SCHEMA_VERSION:
            raise ValueError(
                f"manifest schema v{value} is not supported by this runtime "
                f"(supports <= v{BLOCK_MANIFEST_SCHEMA_VERSION}); upgrade the "
                "runtime or downgrade the block manifest"
            )
        return value

    @field_validator("name")
    @classmethod
    def _name_must_be_slug(cls, value: str) -> str:
        if not _BLOCK_NAME_RE.match(value):
            raise ValueError(
                "name must be a lowercase slug (letters/digits/._-) starting "
                f"with a letter or digit; got {value!r}"
            )
        return value

    @field_validator("provides", "consumes", "emits", "subscribes")
    @classmethod
    def _no_duplicates(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        for item in value:
            if item in seen:
                raise ValueError(f"duplicate entry {item!r} in service/event list")
            seen.add(item)
        return value

    @model_validator(mode="after")
    def _no_self_consumption(self) -> BlockManifest:
        overlap = set(self.provides) & set(self.consumes)
        if overlap:
            raise ValueError(f"{self.name}: cannot both provide and consume {sorted(overlap)}")
        return self

    # ── Loading helpers ──────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BlockManifest:
        """Build a manifest from a parsed dict, with a stable error message."""
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"invalid block manifest: {exc}") from exc

    @classmethod
    def from_yaml(cls, path: str | Path) -> BlockManifest:
        """Load ``block.yaml`` from a block/plugin directory.

        ``yaml`` is imported lazily so the lean desktop core (which ships
        without the ``serve`` extra) can import this module regardless.
        """
        import yaml

        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: block manifest must be a mapping")
        return cls.from_dict(raw)

    @classmethod
    def from_plugin_manifest(
        cls,
        plugin_manifest: Any,
        *,
        kind: str | BlockKind | None = None,
    ) -> BlockManifest:
        """Map an existing ``PluginManifest`` onto a ``BlockManifest``.

        Keeps legacy plugins on the new composition layer without requiring a
        rewrite: ``requires``/``provides``/``subscribes`` map 1:1 and ``kind``
        defaults to :attr:`BlockKind.PLUGIN` unless ``kind`` is given (a
        plugin.yaml ``kind: arm`` makes the block an execution arm).
        """
        return cls(
            name=getattr(plugin_manifest, "name", "unnamed"),
            version=getattr(plugin_manifest, "version", "0.1.0"),
            description=getattr(plugin_manifest, "description", ""),
            author=getattr(plugin_manifest, "author", ""),
            kind=BlockKind(kind) if kind is not None else BlockKind.PLUGIN,
            provides=list(getattr(plugin_manifest, "provides", []) or []),
            consumes=list(getattr(plugin_manifest, "requires", []) or []),
            subscribes=list(getattr(plugin_manifest, "subscribes", []) or []),
        )
