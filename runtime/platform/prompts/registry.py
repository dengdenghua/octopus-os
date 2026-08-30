"""
runtime.platform.prompts.registry · hot-reload-capable prompt registry.

Why a second registry beside ``loader.PromptLoader``?
-----------------------------------------------------

The original ``PromptLoader`` (see ``runtime/platform/prompts/__init__.py``)
loads YAML files lazily, caches forever, and has no concept of variants.
That contract is shipped — call sites in ``runtime/core/cerebrum`` rely on
its lookup precedence — so we can't change it under their feet.

This module is additive: a new ``PromptRegistry`` aimed at the
**editable templates** under ``prompts/`` (Markdown).  The differences:

* Stores raw text (Markdown), not YAML-with-content-key.
* Supports *variants* — alternate phrasings of the same prompt — stored
  one directory deeper so the base directory stays uncluttered.  The
  layout is::

      prompts/
        system_prompt.md           ← base
        variants/
          system_prompt/
            friendly.md
            pragmatic.md

* Honors the ``ui.prompts_hot_reload`` feature flag.  When ON, ``get()``
  consults ``Path.stat().st_mtime_ns`` for every read and re-loads if the
  file is newer than the cached entry.  When OFF, the cache is sticky —
  even if you ``echo "..." > prompts/foo.md`` mid-process, ``get()`` keeps
  serving the version it loaded on first access.

* Round-trip writes via ``set()`` go through ``atomic_write_text`` so a
  crash mid-edit never produces a half-written prompt.  Every overwrite
  rotates the previous version to ``<file>.md.bak`` (free rollback).

* All public methods are thread-safe under a single ``RLock``.  Concurrent
  readers don't tear; a writer briefly stalls readers.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.platform.io import atomic_write_text

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Internal cache record
# ═══════════════════════════════════════════════════════════


@dataclass
class _Entry:
    """One cached prompt body + its source file's mtime (ns)."""

    content: str
    mtime_ns: int
    path: Path


@dataclass(frozen=True)
class _Section:
    """One contributed section of the system prompt (dsh PromptSection)."""

    name: str
    order: int
    text: str | None = None
    provider: Callable[[str | None], str] | None = None
    complete: bool = False


@dataclass(frozen=True)
class _PromptContext:
    """One dynamic runtime-context contribution (dsh PromptContext)."""

    name: str
    order: int
    text: str | None = None
    provider: Callable[[str | None], str] | None = None


class PromptRegistry:
    """A hot-reload-aware registry over a ``prompts/`` directory.

    Parameters
    ----------
    prompts_dir :
        Directory containing base ``*.md`` templates.  Created if it
        doesn't exist on first ``set()``.
    variants_dir :
        Optional override for the variants subtree.  Defaults to
        ``prompts_dir / "variants"``.

    Thread-safety
    -------------
    All public methods acquire ``self._lock`` (an ``RLock``).  Re-entry
    is fine (``get`` may call helpers that also lock).  Writers briefly
    block readers — that is acceptable for an editable-templates store
    that gets touched on the order of single-digit times per second.

    Hot-reload semantics
    --------------------
    ``get()`` and ``list()`` only re-stat the filesystem when
    ``feature_flags.is_on("ui.prompts_hot_reload")`` returns ``True``.
    With the flag OFF, in-memory cache wins absolutely — predictable
    perf, predictable behavior, no surprise reloads in production.
    """

    def __init__(
        self,
        prompts_dir: str | Path,
        *,
        variants_dir: str | Path | None = None,
    ) -> None:
        self._dir = Path(prompts_dir)
        self._variants_dir = (
            Path(variants_dir) if variants_dir is not None else self._dir / "variants"
        )
        self._lock = threading.RLock()
        # Two-level cache:
        #   (name, None)        → base file entry
        #   (name, "variant")   → variant file entry
        self._cache: dict[tuple[str, str | None], _Entry] = {}
        self._scanned = False
        # dsh-style assembly layer (additive; file-template API above
        # stays untouched). Sections/contexts/variables register into a
        # global layer or a named scope layer; the scoped layer shadows
        # the global layer for assembles through that scope.
        self._sections: dict[str, _Section] = {}
        self._scoped_sections: dict[str, dict[str, _Section]] = {}
        self._contexts: dict[str, _PromptContext] = {}
        self._scoped_contexts: dict[str, dict[str, _PromptContext]] = {}
        self._variables: dict[str, Callable[[str | None], str | None]] = {}
        self._scoped_variables: dict[str, dict[str, Callable[[str | None], str | None]]] = {}
        self._suppressed_context_scopes: set[str] = set()

    # ───────────────────────────────────────────────────────
    # Internal helpers
    # ───────────────────────────────────────────────────────

    def _hot_reload_enabled(self) -> bool:
        """``ui.prompts_hot_reload`` flag check.  Imported lazily so the
        module loads even before flag registration in test contexts."""
        try:
            from runtime.platform import feature_flags as _ff
        except (
            ImportError,
            TypeError,
            AttributeError,
            OSError,
        ):  # pragma: no cover - belt and suspenders
            return False
        return _ff.is_on("ui.prompts_hot_reload")

    def _path_for(self, name: str, variant: str | None) -> Path:
        if variant is None:
            return self._dir / f"{name}.md"
        return self._variants_dir / name / f"{variant}.md"

    def _read_path(self, path: Path) -> _Entry | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return None
        return _Entry(
            content=content,
            mtime_ns=stat.st_mtime_ns,
            path=path,
        )

    def _scan_locked(self) -> None:
        """Populate the cache from disk.  Caller must hold ``self._lock``.

        Idempotent: a second call replaces existing entries.
        """
        new_cache: dict[tuple[str, str | None], _Entry] = {}

        # Base prompts
        if self._dir.exists():
            for path in sorted(self._dir.glob("*.md")):
                if not path.is_file():
                    continue
                entry = self._read_path(path)
                if entry is not None:
                    new_cache[(path.stem, None)] = entry

        # Variants — one subdirectory per base name
        if self._variants_dir.exists():
            for name_dir in sorted(self._variants_dir.iterdir()):
                if not name_dir.is_dir():
                    continue
                base_name = name_dir.name
                for vpath in sorted(name_dir.glob("*.md")):
                    if not vpath.is_file():
                        continue
                    entry = self._read_path(vpath)
                    if entry is not None:
                        new_cache[(base_name, vpath.stem)] = entry

        self._cache = new_cache
        self._scanned = True

    def _ensure_scanned_locked(self) -> None:
        if not self._scanned:
            self._scan_locked()

    def _refresh_one_locked(
        self,
        name: str,
        variant: str | None,
    ) -> _Entry | None:
        """Re-stat the file for ``(name, variant)``; reload if mtime
        changed; return the (possibly fresh) entry, or ``None`` if the
        file is gone."""
        path = self._path_for(name, variant)
        try:
            stat = path.stat()
        except OSError:
            self._cache.pop((name, variant), None)
            return None

        cached = self._cache.get((name, variant))
        if cached is None or cached.mtime_ns != stat.st_mtime_ns:
            entry = self._read_path(path)
            if entry is not None:
                self._cache[(name, variant)] = entry
            return entry
        return cached

    # ───────────────────────────────────────────────────────
    # Public API
    # ───────────────────────────────────────────────────────

    def reload(self) -> None:
        """Force a full re-scan from disk, dropping the cache."""
        with self._lock:
            self._scan_locked()

    def get(self, name: str, variant: str | None = None) -> str:
        """Return the prompt body for ``name`` (optionally a variant).

        Variant fallback: if ``variant`` is given but no variant file
        exists, the base ``name`` is returned instead.  Raises
        ``KeyError`` only if neither variant nor base is registered.
        """
        with self._lock:
            self._ensure_scanned_locked()

            hot = self._hot_reload_enabled()

            # First try the variant (if requested)
            if variant is not None:
                if hot:
                    entry = self._refresh_one_locked(name, variant)
                    if entry is not None:
                        return entry.content
                else:
                    cached = self._cache.get((name, variant))
                    if cached is not None:
                        return cached.content
                # fall through to base

            # Base lookup
            if hot:
                entry = self._refresh_one_locked(name, None)
                if entry is not None:
                    return entry.content
            else:
                cached = self._cache.get((name, None))
                if cached is not None:
                    return cached.content

            raise KeyError(
                f"prompt {name!r}"
                + (f" (variant {variant!r})" if variant else "")
                + " not found in registry"
            )

    def list(self) -> list[dict[str, Any]]:
        """List registered prompts.

        Returns a list of dicts shaped::

            {
                "name": "system_prompt",
                "variants": ["friendly", "pragmatic"],
                "modified_at": 1234567890.123,  # base mtime, seconds
            }

        Variants are sorted alphabetically.  ``modified_at`` reflects
        the base file's mtime (or the most-recent variant if no base
        exists).  When the hot-reload flag is ON, this triggers a
        re-scan; otherwise it serves from cache.
        """
        with self._lock:
            self._ensure_scanned_locked()
            if self._hot_reload_enabled():
                self._scan_locked()

            # Bucket by base name
            buckets: dict[str, dict[str, Any]] = {}
            for (name, variant), entry in self._cache.items():
                bucket = buckets.setdefault(
                    name,
                    {"name": name, "variants": [], "modified_at": 0.0},
                )
                if variant is None:
                    bucket["modified_at"] = entry.mtime_ns / 1_000_000_000
                else:
                    bucket["variants"].append(variant)
                    # If no base, surface the variant mtime so callers
                    # have something useful.
                    if bucket["modified_at"] == 0.0:
                        bucket["modified_at"] = entry.mtime_ns / 1_000_000_000

            for bucket in buckets.values():
                bucket["variants"] = sorted(bucket["variants"])
            return sorted(
                buckets.values(),
                key=lambda b: b["name"],
            )

    def set(
        self,
        name: str,
        content: str,
        *,
        variant: str | None = None,
    ) -> None:
        """Write a prompt body to disk atomically and invalidate cache.

        Always uses ``atomic_write_text`` (durable, with ``.bak``
        rollover).  After the write, the cache entry is updated from
        the freshly-stat'd file so subsequent ``get()`` calls return
        the new content even if the hot-reload flag is OFF.
        """
        if not name or any(ch in name for ch in ("/", "\\", "..", "\x00")):
            raise ValueError(f"invalid prompt name {name!r}")
        if variant is not None and (
            not variant or any(ch in variant for ch in ("/", "\\", "..", "\x00"))
        ):
            raise ValueError(f"invalid variant name {variant!r}")

        path = self._path_for(name, variant)
        with self._lock:
            atomic_write_text(path, content)
            entry = self._read_path(path)
            if entry is None:
                # Extremely unlikely (we just wrote it) but guard.
                self._cache.pop((name, variant), None)
            else:
                self._cache[(name, variant)] = entry
            # Make sure list() reflects newly-introduced prompts
            # without forcing a full rescan.
            self._scanned = True

    # ───────────────────────────────────────────────────────
    # dsh-style assembly layer (order / complete / suppress /
    # variables / scope shadow)
    # ───────────────────────────────────────────────────────

    def register_section(
        self,
        name: str,
        *,
        order: int = 0,
        text: str | None = None,
        provider: Callable[[str | None], str] | None = None,
        complete: bool = False,
        scope: str | None = None,
    ) -> Callable[[], None]:
        """Register an ordered prompt section (dsh ``PromptSection``).

        Sections are concatenated in ascending ``order`` (convention:
        -100 harness identity, 0 persona, 100-199 tool guidance).
        ``text`` may contain ``{{variable}}`` references resolved at
        assembly time; ``provider`` is evaluated per assembly with the
        scope key. A ``complete`` section becomes the sole section —
        more than one effective complete section fails the assembly.
        Scoped registrations shadow global same-name sections.
        Returns a disposer that removes the registration.
        """
        if text is None and provider is None:
            raise ValueError(f"section {name!r} needs text or provider")
        section = _Section(
            name=name,
            order=order,
            text=text,
            provider=provider,
            complete=complete,
        )
        with self._lock:
            layer = self._scoped_sections.setdefault(scope, {}) if scope else self._sections
            if name in layer:
                raise ValueError(f"section {name!r} is already registered")
            layer[name] = section
        return self._disposer(layer, name)

    def register_context(
        self,
        name: str,
        *,
        order: int = 0,
        text: str | None = None,
        provider: Callable[[str | None], str] | None = None,
        scope: str | None = None,
    ) -> Callable[[], None]:
        """Register dynamic runtime context (dsh ``PromptContext``).

        Like sections, but suppressible via ``suppress_runtime_context``
        and never allowed to be ``complete``.
        """
        if text is None and provider is None:
            raise ValueError(f"context {name!r} needs text or provider")
        ctx = _PromptContext(name=name, order=order, text=text, provider=provider)
        with self._lock:
            layer = self._scoped_contexts.setdefault(scope, {}) if scope else self._contexts
            if name in layer:
                raise ValueError(f"context {name!r} is already registered")
            layer[name] = ctx
        return self._disposer(layer, name)

    def register_variable(
        self,
        name: str,
        provider: Callable[[str | None], str | None],
        *,
        scope: str | None = None,
    ) -> Callable[[], None]:
        """Register a prompt variable (dsh ``variable``).

        The provider is evaluated per assembly; returning ``None``
        makes rendering any section that references the name fail.
        Scoped values shadow globals.
        """
        with self._lock:
            layer = self._scoped_variables.setdefault(scope, {}) if scope else self._variables
            if name in layer:
                raise ValueError(f"variable {name!r} is already registered")
            layer[name] = provider
        return self._disposer(layer, name)

    def suppress_runtime_context(
        self,
        *,
        scope: str | None = None,
    ) -> Callable[[], None]:
        """Suppress every dynamic runtime-context contribution in a
        scope (dsh ``suppressRuntimeContext``). A global suppression
        (no scope) applies to every scope; a scoped suppression only
        shadows for that scope. Sections are never suppressed.
        Returns a disposer that lifts the suppression."""
        key = scope if scope is not None else ""
        with self._lock:
            self._suppressed_context_scopes.add(key)

        def unsuppress() -> None:
            with self._lock:
                self._suppressed_context_scopes.discard(key)

        return unsuppress

    def assemble(
        self,
        *,
        scope: str | None = None,
        include_runtime_contexts: bool = True,
    ) -> str:
        """Assemble the effective system prompt for a scope
        (dsh ``SystemPrompt.assemble``).

        Scoped sections/contexts/variables shadow global ones. With no
        effective ``complete`` section, contributions join in ascending
        order; otherwise the complete section is restored as the sole
        prompt. ``{{variable}}`` references are interpolated after
        ordering; an unresolvable reference fails the assembly.
        """
        with self._lock:
            sections = self._merged(self._sections, self._scoped_sections, scope)
            complete = [s for s in sections.values() if s.complete]
            if len(complete) > 1:
                names = ", ".join(sorted(s.name for s in complete))
                raise ValueError(f"more than one complete section: {names}")
            if complete:
                body = self._render_section(complete[0], scope)
            else:
                parts = [
                    self._render_section(s, scope)
                    for s in sorted(sections.values(), key=lambda s: (s.order, s.name))
                ]
                if include_runtime_contexts and not self._contexts_suppressed(scope):
                    contexts = self._merged(self._contexts, self._scoped_contexts, scope)
                    parts.extend(
                        self._render_section(c, scope)
                        for c in sorted(contexts.values(), key=lambda c: (c.order, c.name))
                    )
                body = "\n\n".join(part for part in parts if part)
            return self.render(body, scope=scope)

    def render(self, text: str, *, scope: str | None = None) -> str:
        """Interpolate ``{{variable}}`` references (dsh variable
        rendering). An unknown name or a ``None`` value fails loud."""
        with self._lock:

            def replace(match: re.Match[str]) -> str:
                name = match.group(1)
                provider = self._variable_provider(name, scope)
                if provider is None:
                    raise ValueError(f"prompt variable {name!r} is not registered")
                value = provider(scope)
                if value is None:
                    raise ValueError(f"prompt variable {name!r} resolved to None")
                return str(value)

            return _VARIABLE_RE.sub(replace, text)

    def sections(
        self,
        *,
        scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """Enumerate effective sections for a scope (debugging / UI)."""
        with self._lock:
            merged = self._merged(self._sections, self._scoped_sections, scope)
            return [
                {
                    "name": s.name,
                    "order": s.order,
                    "complete": s.complete,
                    "dynamic": s.provider is not None,
                }
                for s in sorted(merged.values(), key=lambda s: (s.order, s.name))
            ]

    # ── assembly internals ────────────────────────────────

    @staticmethod
    def _merged(
        global_layer: dict[str, Any],
        scoped_layers: dict[str, dict[str, Any]],
        scope: str | None,
    ) -> dict[str, Any]:
        """Global entries (insertion order) then scoped shadows."""
        if scope is None:
            return dict(global_layer)
        merged = dict(global_layer)
        merged.update(scoped_layers.get(scope, {}))
        return merged

    def _render_section(
        self,
        section: _Section | _PromptContext,
        scope: str | None,
    ) -> str:
        if section.text is not None:
            return section.text
        if section.provider is not None:
            value = section.provider(scope)
            return value if value is not None else ""
        return ""

    def _variable_provider(
        self,
        name: str,
        scope: str | None,
    ) -> Callable[[str | None], str | None] | None:
        if scope is not None:
            scoped = self._scoped_variables.get(scope, {}).get(name)
            if scoped is not None:
                return scoped
        return self._variables.get(name)

    def _contexts_suppressed(self, scope: str | None) -> bool:
        key = scope if scope is not None else ""
        return "" in self._suppressed_context_scopes or key in self._suppressed_context_scopes

    @staticmethod
    def _disposer(layer: dict[str, Any], name: str) -> Callable[[], None]:
        def dispose() -> None:
            layer.pop(name, None)

        return dispose


__all__ = ["PromptRegistry"]


_VARIABLE_RE = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
