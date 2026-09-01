"""Meta-skill workflow orchestration.

A MetaSkill is a reusable workflow template that chains multiple
Skills into a multi-step pipeline. Unlike a single ``apply_skill``
call, a MetaSkill captures the WHOLE PROCESS — what to do first,
what feeds into what, and what the final deliverable looks like.

Design rationale
----------------

``runtime/memory/skill_library.py`` exposes
``learn_skill_from_text`` + ``apply_skill`` · a single skill template
that produces one document. The MetaSkill layer elevates this to a
*workflow*: research → outline → write → compile → publish,
where each step's output feeds the next.

This module reuses the existing runtime instead of duplicating it:

* **Reuses ``TaskGraph``** (runtime/platform/models/pipeline.py) as
  the execution graph. MetaSkill compiles to a TaskGraph and is
  executed by the existing ``GraphRuntime`` · no new scheduler.
* **Reuses ``SkillId`` / ``ArmPool``** · the SkillCurator /
  extension registry stays the source of truth for which skills
  are available. MetaSkill just names them.
* **Reuses the SkillLibrary** · the frontmatter format
  (``name / description / when_to_use / steps``) mirrors
  ``agents/<id>/skills/*.md`` so the existing UI / CLI /
  ``list_learned_skills`` introspection can be applied to
  MetaSkills unchanged.

The trade-off
~~~~~~~~~~~~~

A pure YAML/JSON template is more verbose than a natural-language
"let the LLM figure it out" approach, but it's:

* **Inspectable** · operators can see the exact step ordering
  before execution
* **Auditable** · each step becomes a TaskNode with its own
  retry / timeout / arm assignment
* **Replayable** · a failed MetaSkill can be re-run from any
  completed step
* **Cost-controllable** · ``BudgetSpec`` is bound to the whole
  pipeline, matching the ``Recipe`` pattern
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

_LOG = logging.getLogger("echo.meta_skill")

StepKind = Literal["sucker", "subgraph", "validator", "branch", "merger", "arm"]
EdgeKind = Literal["normal", "branch", "chromatophore"]


# ── Template format ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MetaStep:
    """One step inside a MetaSkill workflow.

    Mirrors the subset of ``TaskNode`` that an operator would
    hand-author in a YAML/JSON workflow template. The full
    ``TaskNode`` is built later by ``compile_to_task_graph``.
    """

    node_id: str
    skill_ref: str
    args_template: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    failure_retry: int = 0
    timeout_ms: int = 30_000
    kind: StepKind = "sucker"

    def __post_init__(self) -> None:
        # node_id must be a short slug so it can be referenced as
        # ``{n0.output}`` from later steps.
        if not re.match(r"^[a-z][a-z0-9_]{0,31}$", self.node_id):
            raise ValueError(
                f"step node_id must match ^[a-z][a-z0-9_]{{0,31}}$, got {self.node_id!r}"
            )
        if not self.skill_ref or not self.skill_ref.strip():
            raise ValueError(f"step {self.node_id!r} has empty skill_ref")


@dataclass(frozen=True, slots=True)
class MetaEdge:
    """An edge in the MetaSkill workflow.

    Carries the same semantics as ``WorkflowEdge`` but lives at the
    template layer where the operator sees it. Translation to
    ``WorkflowEdge`` happens during compile.
    """

    from_node: str
    to_node: str
    kind: EdgeKind = "normal"
    condition: str | None = None


@dataclass(frozen=True, slots=True)
class MetaSkill:
    """A complete MetaSkill workflow template.

    The Python class is named ``MetaSkill`` for greppability across
    the codebase. The user-facing label is **能力包 / Skill Cluster** —
    a named bundle of skills wired together as a reusable workflow.

    The two names are deliberately kept side-by-side:

    * ``MetaSkill`` (class) — Python import path, greppable in the codebase
    * ``kind="skill_cluster"`` (YAML field) — what operators see in
      YAML frontmatter and what the UI shows as "能力包"

    Compiles to a ``TaskGraph`` for execution. The same instance
    can be re-compiled and re-run many times with different
    ``user_input`` · the cost is just one validator pass + one
    graph build.
    """

    name: str
    description: str = ""
    when_to_use: str = ""
    affinity: tuple[str, ...] = field(default_factory=tuple)
    steps: tuple[MetaStep, ...] = field(default_factory=tuple)
    edges: tuple[MetaEdge, ...] = field(default_factory=tuple)
    budget_tokens: int = 100_000
    budget_usd: float = 1.0
    budget_latency_ms: int = 600_000
    version: str = "0.1.0"
    author: str = "echo-agent"
    learned_at: str = ""
    # User-facing label. ``"skill_cluster"`` is the default. The value
    # is what the UI / CLI shows, so it can be swapped to other
    # workflow kinds later (e.g. ``"recipe"``, ``"macro"``) without
    # renaming the Python class.
    kind: str = "skill_cluster"

    def __post_init__(self) -> None:
        if not re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", self.name):
            raise ValueError(
                f"meta-skill name must match ^[a-z0-9][a-z0-9_-]{{0,63}}$, got {self.name!r}"
            )
        if not self.steps:
            raise ValueError(f"meta-skill {self.name!r} has no steps")
        node_ids = {s.node_id for s in self.steps}
        if len(node_ids) != len(self.steps):
            dups = [
                s.node_id
                for s in self.steps
                if sum(1 for x in self.steps if x.node_id == s.node_id) > 1
            ]
            raise ValueError(
                f"meta-skill {self.name!r} has duplicate step ids: {sorted(set(dups))}"
            )
        for edge in self.edges:
            if edge.from_node not in node_ids:
                raise ValueError(
                    f"edge {edge.from_node!r}->{edge.to_node!r}: "
                    f"from_node not in steps {sorted(node_ids)}"
                )
            if edge.to_node not in node_ids:
                raise ValueError(
                    f"edge {edge.from_node!r}->{edge.to_node!r}: "
                    f"to_node not in steps {sorted(node_ids)}"
                )
        # depends_on must reference earlier steps · meta-skills are
        # forward-only (no back-edges) because we want a DAG.
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in node_ids:
                    raise ValueError(f"step {step.node_id!r} depends_on unknown step {dep!r}")
                if dep == step.node_id:
                    raise ValueError(f"step {step.node_id!r} cannot depend on itself")


# ── YAML / JSON loader ─────────────────────────────────────


def meta_skill_from_dict(data: dict[str, Any]) -> MetaSkill:
    """Build a ``MetaSkill`` from a parsed YAML/JSON mapping.

    Expected shape::

        name: paper-write
        description: ...
        when_to_use: ...
        affinity: [research, writing]
        budget:
          tokens: 100000
          usd: 5.0
          latency_ms: 3600000
        steps:
          - node_id: research
            skill: deep_research
            args: { topic: "{user_input.topic}" }
            depends_on: []
          - node_id: outline
            skill: docx_outline
            args: { research: "{n_research.output}" }
            depends_on: [research]
        edges:
          - { from: research, to: outline }

    ``user_input.<field>`` and ``<step>.output`` are the two
    template families operators can reference.
    """
    if not isinstance(data, dict):
        raise ValueError(f"meta-skill doc must be a mapping, got {type(data).__name__}")
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("meta-skill doc is missing required 'name'")

    raw_steps = data.get("steps") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError(f"meta-skill {name!r}: 'steps' must be a non-empty list")

    steps: list[MetaStep] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            raise ValueError(
                f"meta-skill {name!r}: each step must be a mapping, got {type(raw).__name__}"
            )
        node_id = str(raw.get("node_id") or raw.get("id") or "").strip()
        if not node_id:
            raise ValueError(f"meta-skill {name!r}: step missing node_id")
        skill_ref = str(raw.get("skill") or raw.get("skill_ref") or "").strip()
        steps.append(
            MetaStep(
                node_id=node_id,
                skill_ref=skill_ref,
                args_template=dict(raw.get("args") or {}),
                depends_on=tuple(raw.get("depends_on") or ()),
                failure_retry=int(raw.get("failure_retry") or 0),
                timeout_ms=int(raw.get("timeout_ms") or 30_000),
                kind=raw.get("kind") or "sucker",
            )
        )

    raw_edges = data.get("edges") or []
    edges: list[MetaEdge] = []
    for raw in raw_edges:
        if not isinstance(raw, dict):
            continue
        edges.append(
            MetaEdge(
                from_node=str(raw.get("from") or raw.get("from_node") or "").strip(),
                to_node=str(raw.get("to") or raw.get("to_node") or "").strip(),
                kind=raw.get("kind") or "normal",
                condition=raw.get("condition"),
            )
        )

    budget = data.get("budget") or {}
    if not isinstance(budget, dict):
        budget = {}

    return MetaSkill(
        name=name,
        description=str(data.get("description") or "").strip(),
        when_to_use=str(data.get("when_to_use") or "").strip(),
        affinity=tuple(data.get("affinity") or ()),
        steps=tuple(steps),
        edges=tuple(edges),
        budget_tokens=int(budget.get("tokens") or 100_000),
        budget_usd=float(budget.get("usd") or 1.0),
        budget_latency_ms=int(budget.get("latency_ms") or 600_000),
        version=str(data.get("version") or "0.1.0"),
        author=str(data.get("author") or "echo-agent"),
        learned_at=str(data.get("learned_at") or ""),
        kind=str(data.get("kind") or "skill_cluster").strip() or "skill_cluster",
    )


def meta_skill_from_yaml_text(text: str) -> MetaSkill:
    """Parse a YAML (or JSON, since YAML is a superset) string.

    We import PyYAML lazily so the module remains importable in
    minimal environments (e.g. CLI smoke tests) where the YAML
    dependency is optional.
    """
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover - yaml is in pyproject deps
        # Fall back to a minimal JSON parse if YAML fails. We try
        # the YAML library first because the canonical authoring
        # format is YAML.
        return meta_skill_from_dict(json.loads(text))

    parsed = yaml.safe_load(text)
    return meta_skill_from_dict(parsed or {})


# ── TaskGraph compile ──────────────────────────────────────


# Token used to interpolate a previous step's output. Format is
# the same one GraphRuntime's ``_lookup`` already understands:
# the first segment must be a valid Python identifier. We canonicalize
# the friendly alias through ``alias_to_id`` so the rewritten ref
# still points at the step's actual ``node_id`` in the runtime's
# ``outputs_by_node`` dict.
def _rewrite_template_refs(
    args: dict[str, Any],
    alias_to_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Rewrite ``{friendly.output}`` → ``{step_node_id.output}``.

    Operators are allowed to use friendly names like ``{research.output}``
    in the YAML; we canonicalize them to ``{step_node_id.output}`` so
    GraphRuntime can resolve them against the step's actual node_id
    in the ``outputs_by_node`` dict.

    ``alias_to_id`` maps the friendly name to the canonical step id.
    When the step's node_id IS the alias, this is a no-op rewrite.
    """
    if alias_to_id is None:
        alias_to_id = {}

    def _replacer(value: Any) -> Any:
        if isinstance(value, str):
            return re.sub(
                r"\{([a-zA-Z_][a-zA-Z0-9_]*)\.output\}",
                lambda m: "{" + alias_to_id.get(m.group(1), m.group(1)) + ".output}",
                value,
            )
        if isinstance(value, dict):
            return {k: _replacer(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_replacer(v) for v in value]
        return value

    return _replacer(args)


def compile_to_task_graph(
    meta: MetaSkill,
    *,
    user_input: dict[str, Any] | None = None,
) -> Any:
    """Compile a ``MetaSkill`` to a runtime ``TaskGraph``.

    Returns a fully-formed ``TaskGraph`` ready to be passed to
    ``GraphRuntime.run``. We import the model types lazily to
    avoid a hard import cycle with ``runtime.platform.models``.

    ``user_input`` is the dictionary an operator binds to the
    ``{user_input.<key>}`` placeholders. Keys are merged into the
    node's ``args_template`` so the runtime sees a fully-resolved
    args dict.
    """
    from runtime.platform.models import (
        BudgetSpec,
        SkillId,
        TaskGraph,
        TaskNode,
        WorkflowEdge,
    )

    user_input = dict(user_input or {})

    # Build alias map so step ids in args can be referenced by name
    # (e.g. ``{research.output}`` instead of ``{n_research.output}``).
    # The friendly name == the canonical id, so this is a self-map.
    step_by_id = {s.node_id: s for s in meta.steps}
    alias_to_id: dict[str, str] = {nid: nid for nid in step_by_id}

    nodes: list[TaskNode] = []
    edges: list[WorkflowEdge] = []

    for step in meta.steps:
        args = dict(step.args_template)
        # 1. Rewrite friendly aliases to canonical TaskGraph refs.
        args = _rewrite_template_refs(args, alias_to_id)
        # 2. Inject user_input bindings.
        for k, v in user_input.items():
            args.setdefault(f"user_input.{k}", v)

        nodes.append(
            TaskNode(
                node_id=step.node_id,
                kind=step.kind,
                skill_ref=SkillId(step.skill_ref),
                args_template=args,
                failure_retry=step.failure_retry,
                timeout_ms=step.timeout_ms,
            )
        )

        # 3. Edges derived from depends_on (or from explicit edges).
        for dep in step.depends_on:
            edges.append(
                WorkflowEdge(
                    from_node=dep,
                    to_node=step.node_id,
                    kind="normal",
                )
            )

    for e in meta.edges:
        edges.append(
            WorkflowEdge(
                from_node=e.from_node,
                to_node=e.to_node,
                kind=e.kind,
                condition=e.condition,
            )
        )

    return TaskGraph(
        nodes=nodes,
        edges=edges,
        budget=BudgetSpec(
            tokens=meta.budget_tokens,
            usd=meta.budget_usd,
            latency_ms=meta.budget_latency_ms,
        ),
        task_type=f"meta_skill:{meta.name}",
        strategy="meta_skill_compiled",
        recipe_hash=None,
    )


# ── Storage paths ──────────────────────────────────────────


def _project_root() -> Path:
    from runtime.platform.process.paths import project_root

    return project_root()


def meta_skills_read_dirs(scope: str = "global") -> list[Path]:
    """Directories to READ MetaSkill templates from, in precedence order.

    Writes go to one place (:func:`meta_skills_dir`), but reads must also see
    the templates shipped in the repo. ``project_root()`` walks up from the
    working directory, so a process started outside the checkout resolved
    ``<cwd>/meta_skills`` and listed nothing — the 18 tracked capability
    packages silently disappeared with no error.

    The writable directory comes first so a user's own edit of a shipped name
    wins over the bundled copy.
    """
    dirs = [meta_skills_dir(scope)]
    try:
        from runtime.platform.process.paths import resources_root

        bundled = _scoped_dir(resources_root(), scope)
    except Exception:  # noqa: BLE001 — never let path resolution break listing
        return dirs
    if bundled not in dirs:
        dirs.append(bundled)
    return dirs


def _scoped_dir(root: Path, scope: str) -> Path:
    """``<root>/meta_skills`` or ``<root>/agents/<id>/meta_skills``."""
    if scope.startswith("agent:"):
        agent_id = scope.split(":", 1)[1].strip()
        if not agent_id:
            raise ValueError(f"invalid agent scope: {scope!r}")
        return root / "agents" / agent_id / "meta_skills"
    return root / "meta_skills"


def meta_skills_dir(scope: str = "global") -> Path:
    """Where ``*.yaml`` MetaSkill templates are stored.

    ``scope='global'`` lives at ``<root>/meta_skills/`` (shared).
    ``scope='agent:<id>'`` lives at ``<root>/agents/<id>/meta_skills/``
    (per-agent, matches the per-agent ``skills/`` layout used by
    ``runtime/memory/skill_library.py``).
    """
    root = _project_root()
    if scope.startswith("agent:"):
        agent_id = scope.split(":", 1)[1].strip()
        if not agent_id:
            raise ValueError(f"invalid agent scope: {scope!r}")
        return root / "agents" / agent_id / "meta_skills"
    return root / "meta_skills"


def list_meta_skills(scope: str = "global") -> list[dict[str, Any]]:
    """Enumerate MetaSkill templates on disk.

    Returns a list of ``{name, file, description, steps, kind}`` dicts
    — same shape as ``list_learned_skills`` so existing UI can render
    MetaSkill catalog entries with no changes. ``kind`` defaults to
    ``"skill_cluster"`` (UI label "能力包").
    """
    # First directory wins on a name collision, so a user's own copy shadows
    # the bundled template of the same name rather than appearing twice.
    seen: set[str] = set()
    candidates: list[Path] = []
    for sdir in meta_skills_read_dirs(scope):
        if not sdir.exists():
            continue
        for p in sorted(sdir.glob("*.yaml")):
            if p.name in seen:
                continue
            seen.add(p.name)
            candidates.append(p)
    if not candidates:
        return []
    out: list[dict[str, Any]] = []
    for p in candidates:
        try:
            text = p.read_text(encoding="utf-8")
            meta = meta_skill_from_yaml_text(text)
            out.append(
                {
                    "name": meta.name,
                    "file": p.name,
                    "path": str(p),
                    "description": meta.description,
                    "when_to_use": meta.when_to_use,
                    "affinity": list(meta.affinity),
                    "steps": [s.node_id for s in meta.steps],
                    "step_count": len(meta.steps),
                    "budget_tokens": meta.budget_tokens,
                    "budget_usd": meta.budget_usd,
                    "budget_latency_ms": meta.budget_latency_ms,
                    "version": meta.version,
                    "kind": meta.kind,
                    "display_name": _display_name(meta),
                }
            )
        except (OSError, ValueError, TypeError):
            continue
    return out


_DISPLAY_NAMES: dict[str, str] = {
    "skill_cluster": "能力包",
    "recipe": "配方",
    "macro": "宏",
}


def _display_name(meta: MetaSkill) -> str:
    """Map a ``kind`` to a user-facing display name.

    Falls back to the raw kind (capitalised) when we don't have a
    Chinese label — better than a hard error.
    """
    return _DISPLAY_NAMES.get(meta.kind, meta.kind.replace("_", " ").title())


def load_meta_skill(name: str, scope: str = "global") -> MetaSkill | None:
    """Read a single MetaSkill by name. ``None`` if not found."""
    for sdir in meta_skills_read_dirs(scope):
        for ext in (".yaml", ".yml", ".json"):
            path = sdir / f"{name}{ext}"
            if path.exists():
                return meta_skill_from_yaml_text(path.read_text(encoding="utf-8"))
    return None


def save_meta_skill(meta: MetaSkill, scope: str = "global") -> Path:
    """Persist a MetaSkill as ``<name>.yaml``.

    Writes a short YAML header (name, author, when-to-use, step
    chain) before the structured body. Overwrites if it already
    exists.
    """
    sdir = meta_skills_dir(scope)
    sdir.mkdir(parents=True, exist_ok=True)
    path = sdir / f"{meta.name}.yaml"

    body = _to_yaml(meta)
    header = (
        f"# Meta-skill: {meta.name} (v{meta.version})\n"
        f"# Author: {meta.author}\n"
        f"# When to use: {meta.when_to_use or '(none)'}\n"
        f"# Steps: {' -> '.join(s.node_id for s in meta.steps)}\n"
    )
    path.write_text(header + "\n" + body, encoding="utf-8")
    return path


def _to_yaml(meta: MetaSkill) -> str:
    """Serialize a MetaSkill to YAML.

    We hand-roll a minimal YAML emitter to avoid pulling in a
    full serializer dependency for what is fundamentally a
    flat list of mappings.
    """
    lines: list[str] = []
    lines.append(f"kind: {meta.kind}")
    lines.append(f"name: {meta.name}")
    lines.append(f"version: {meta.version}")
    if meta.description:
        lines.append(f'description: "{_escape_yaml_str(meta.description)}"')
    if meta.when_to_use:
        lines.append(f'when_to_use: "{_escape_yaml_str(meta.when_to_use)}"')
    if meta.affinity:
        lines.append("affinity:")
        for tag in meta.affinity:
            lines.append(f"  - {tag}")
    lines.append("budget:")
    lines.append(f"  tokens: {meta.budget_tokens}")
    lines.append(f"  usd: {meta.budget_usd}")
    lines.append(f"  latency_ms: {meta.budget_latency_ms}")
    lines.append("steps:")
    for step in meta.steps:
        lines.append(f"  - node_id: {step.node_id}")
        lines.append(f"    skill: {step.skill_ref}")
        if step.depends_on:
            lines.append(f"    depends_on: [{', '.join(step.depends_on)}]")
        if step.failure_retry:
            lines.append(f"    failure_retry: {step.failure_retry}")
        if step.timeout_ms != 30_000:
            lines.append(f"    timeout_ms: {step.timeout_ms}")
        if step.kind != "sucker":
            lines.append(f"    kind: {step.kind}")
        if step.args_template:
            lines.append("    args:")
            for k, v in step.args_template.items():
                lines.append(f"      {k}: {_yaml_value(v)}")
    if meta.edges:
        lines.append("edges:")
        for e in meta.edges:
            line = f"  - {{ from: {e.from_node}, to: {e.to_node}"
            if e.kind != "normal":
                line += f", kind: {e.kind}"
            if e.condition:
                line += f", condition: {_yaml_value(e.condition)}"
            line += " }"
            lines.append(line)
    return "\n".join(lines) + "\n"


def _yaml_value(v: Any) -> str:
    """Render a Python value as a single-line YAML scalar.

    Strings are quoted; numbers / bools are bare; dicts/lists
    use JSON syntax (YAML is a JSON superset).
    """
    if isinstance(v, str):
        return f'"{_escape_yaml_str(v)}"'
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(v, ensure_ascii=False)


def _escape_yaml_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ── Pick a MetaSkill for a request ──────────────────────────


def match_meta_skill(
    request: str,
    *,
    available: Iterable[MetaSkill] | None = None,
) -> MetaSkill | None:
    """Cheap, deterministic, no-LLM MetaSkill selector.

    Mirrors ``list_learned_skills`` + ``apply_skill`` in spirit:
    operators hand-author a ``when_to_use`` trigger phrase, and we
    look for keyword overlap with the incoming ``request``. The
    first MetaSkill whose ``when_to_use`` shares ≥2 tokens with
    the request wins. This is intentionally dumb — the LLM-driven
    route is the ``when_to_use`` author writing good triggers, not
    a hidden scoring model.

    Returns ``None`` if no good match — callers can fall back to
    the existing single-skill ``apply_skill`` path.
    """
    if not request or not request.strip():
        return None

    if available is None:
        available = [load_meta_skill(d["name"]) for d in list_meta_skills()]
        available = [m for m in available if m is not None]

    request_tokens = _tokenize(request)
    if not request_tokens:
        return None

    best: tuple[int, MetaSkill] | None = None
    for meta in available:
        if not meta.when_to_use:
            continue
        trigger_tokens = _tokenize(meta.when_to_use)
        if not trigger_tokens:
            continue
        overlap = len(request_tokens & trigger_tokens)
        if overlap < 2:
            continue
        if best is None or overlap > best[0]:
            best = (overlap, meta)
    return best[1] if best else None


def _tokenize(text: str) -> set[str]:
    """Tiny tokenizer · lowercase, split on non-word, drop short tokens.

    Supports CJK: each Chinese character becomes its own token so
    ``写论文`` matches ``论文`` triggers in the YAML.
    """
    text = text.lower()
    out: set[str] = set()
    # Latin / digit chunks
    for tok in re.findall(r"[a-z0-9_]+", text):
        if len(tok) >= 2:
            out.add(tok)
    # CJK chars individually
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" and ch.strip():
            out.add(ch)
    return out


__all__ = [
    "MetaEdge",
    "MetaSkill",
    "MetaStep",
    "compile_to_task_graph",
    "display_name_for_kind",
    "list_meta_skills",
    "load_meta_skill",
    "match_meta_skill",
    "meta_skill_from_dict",
    "meta_skill_from_yaml_text",
    "meta_skills_dir",
    "meta_skills_read_dirs",
    "save_meta_skill",
]


def display_name_for_kind(kind: str) -> str:
    """Public helper: map a workflow ``kind`` to its UI display name.

    Exposed for the frontend (and any other consumer) that wants to
    show the user-facing label rather than the raw kind slug.
    """
    return _DISPLAY_NAMES.get(kind, kind.replace("_", " ").title())


# ═══════════════════════════════════════════════════════════
# Mermaid renderer
# ═══════════════════════════════════════════════════════════


_MERMAID_MAX_ARG_LEN = 60
_MERMAID_MAX_ARGS_SHOWN = 2


def _format_arg_value(value: object, *, max_len: int = _MERMAID_MAX_ARG_LEN) -> str:
    """Render a single arg value as a short single-line string for Mermaid.

    - Truncates to ``max_len`` with ``…`` suffix.
    - Replaces newlines with spaces (Mermaid labels are single-line per
      ``<br/>`` segment).
    - Wraps strings containing template refs (``{…}``) unchanged so
      users can see the wiring at a glance.
    """
    if isinstance(value, str):
        text = value.replace("\n", " ").replace("\r", " ")
        if len(text) > max_len:
            return text[: max_len - 1] + "…"
        return text
    text = repr(value)
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def _summarize_args(args: Mapping[str, object]) -> str:
    """Render the first N args as ``key=value, key2=value2`` for labels."""
    if not args:
        return ""
    items = list(args.items())[:_MERMAID_MAX_ARGS_SHOWN]
    parts = [f"{k}={_format_arg_value(v)}" for k, v in items]
    if len(args) > _MERMAID_MAX_ARGS_SHOWN:
        parts.append(f"+{len(args) - _MERMAID_MAX_ARGS_SHOWN}")
    return ", ".join(parts)


def _adjacency(steps: Sequence[MetaStep]) -> dict[str, list[str]]:
    """Build ``{node_id: [downstream_ids]}`` for the renderer.

    Derived from ``depends_on`` edges. A node with no entry in the
    returned dict is a root; a node whose list is empty is a sink.
    """
    out: dict[str, list[str]] = {step.node_id: [] for step in steps}
    for step in steps:
        for parent in step.depends_on:
            out.setdefault(parent, []).append(step.node_id)
    return out


def _depth_levels(steps: Sequence[MetaStep]) -> list[list[str]]:
    """Group steps by longest-path depth from a root.

    Two steps that have no dependency between them AND share the same
    depth get rendered on the same Mermaid rank — that's how Mermaid
    visually conveys "these run in parallel". Without this grouping
    the renderer would place independent steps in dependency-list
    order, which often happens to be vertical for the LR layout.

    Returns a list of buckets ordered by depth: ``[[roots], [d=1...]]``.
    Cycles aren't possible (the dataclass validator would have
    rejected the workflow), but unreachable nodes — if they exist —
    fall through into a synthetic last bucket so we never drop a
    step from the diagram.
    """
    by_id = {s.node_id: s for s in steps}
    depth: dict[str, int] = {}
    # Topological evaluation: a step's depth = max(parent depth) + 1.
    # Unresolved deps (parent not in the dict) clamp to 0 — broken
    # references are surfaced by validators, not the renderer.
    pending = list(steps)
    safety = len(pending) * len(pending) + 1  # quadratic upper bound
    while pending and safety > 0:
        safety -= 1
        progressed = False
        for step in list(pending):
            parents = step.depends_on or []
            if all(p in depth for p in parents if p in by_id):
                if not parents:
                    depth[step.node_id] = 0
                else:
                    depth[step.node_id] = (
                        max(
                            (depth.get(p, 0) for p in parents if p in by_id),
                            default=0,
                        )
                        + 1
                    )
                pending.remove(step)
                progressed = True
        if not progressed:
            # Stuck — pour the remainder into the deepest bucket so
            # they still render. Shouldn't happen in practice.
            fallback = max(depth.values(), default=-1) + 1
            for step in pending:
                depth[step.node_id] = fallback
            break

    buckets: dict[int, list[str]] = {}
    for nid, d in depth.items():
        buckets.setdefault(d, []).append(nid)
    return [sorted(buckets[d]) for d in sorted(buckets.keys())]


def meta_skill_to_mermaid(
    meta: MetaSkill,
    *,
    direction: str = "LR",
    include_budget: bool = True,
) -> str:
    """Render a ``MetaSkill`` as a Mermaid ``flowchart`` string.

    The result is suitable for pasting into any Markdown / GitHub /
    docs site that supports Mermaid (e.g. ``mermaid.live``,
    GitHub ``.md`` files, Notion, Obsidian).

    Visual conventions:
        - ``direction`` is ``"LR"`` (left-to-right, default) or
          ``"TD"`` (top-down). Mermaid accepts both.
        - Each node label is a 3-line block:
          ``<node_id>`` / ``<skill_ref>`` / ``key=value, …``.
        - Root nodes (no parents) get a green fill.
        - Sink nodes (no children) get an orange fill.
        - If ``include_budget``, a footer comment lists tokens /
          USD / latency budget so reviewers can see cost at a glance.

    Example output::

        flowchart LR
            classDef root fill:#d4f4d4,stroke:#2e7d32
            classDef sink fill:#ffe0b2,stroke:#ef6c00
            s1["s1<br/>echo<br/>value=42"]:::root
            s2["s2<br/>add<br/>a={s1.output.echoed}, b=8"]
            s3["s3<br/>final<br/>trigger={s2.output.sum}"]:::sink
            s1 --> s2
            s2 --> s3
            %% budget: 60k tokens, $1.50, 30m
    """
    if direction not in ("LR", "TD", "RL", "BT"):
        raise ValueError(f"invalid direction {direction!r}: must be LR / TD / RL / BT")

    steps = list(meta.steps)
    children = _adjacency(steps)
    roots = sorted(s.node_id for s in steps if not s.depends_on)
    sinks = sorted(nid for nid, kids in children.items() if not kids)

    lines: list[str] = [f"flowchart {direction}"]
    # classDef — must come AFTER `flowchart` and BEFORE node declarations
    # in some Mermaid renderers, but it's safe at the top.
    lines.append("    classDef root fill:#d4f4d2,stroke:#2e7d32,color:#1b5e20")
    lines.append("    classDef sink fill:#ffe0b2,stroke:#ef6c00,color:#bf360c")
    lines.append("    classDef bridge fill:#e3f2fd,stroke:#1565c0,color:#0d47a1")

    # Node declarations
    for step in steps:
        role = "root" if step.node_id in roots else ("sink" if step.node_id in sinks else "bridge")
        args_summary = _summarize_args(step.args_template)
        # Mermaid ``["..."]`` labels — escape internal quotes.
        safe_skill = step.skill_ref.replace('"', '\\"')
        if args_summary:
            label = f"{step.node_id}<br/>{safe_skill}<br/>{args_summary.replace(chr(34), '&quot;')}"
        else:
            label = f"{step.node_id}<br/>{safe_skill}"
        lines.append(f'    {step.node_id}["{label}"]:::{role}')

    # Parallel-rank grouping. Wrap each depth level that contains >1
    # node in an invisible-ish subgraph so Mermaid lays its members
    # out side-by-side. Single-node levels are skipped (subgraph with
    # one child reads as visual noise). The subgraph's title carries
    # ``parallel · N tasks`` so reviewers see at a glance which steps
    # the runtime can fan out.
    levels = _depth_levels(steps)
    for level_idx, bucket in enumerate(levels):
        if len(bucket) <= 1:
            continue
        sg_id = f"par_lvl_{level_idx}"
        lines.append(f'    subgraph {sg_id}["⚡ parallel · {len(bucket)} tasks"]')
        # ``direction LR`` inside the subgraph keeps members on a row
        # even when the outer flow is TD.
        lines.append("        direction LR")
        for nid in bucket:
            lines.append(f"        {nid}")
        lines.append("    end")
        # Style the subgraph border lightly so it reads as a hint,
        # not a hard boundary.
        lines.append(
            f"    style {sg_id} fill:#fafafa,stroke:#c8c8c8,stroke-dasharray:3 3,color:#666"
        )

    # Edges — for each step, one arrow per parent in ``depends_on``.
    for step in steps:
        for parent in step.depends_on:
            lines.append(f"    {parent} --> {step.node_id}")

    # Subgraph for affinity / kind so the top of the diagram
    # is human-readable.
    affinity_str = ", ".join(meta.affinity) or "—"
    lines.append(f'    subgraph meta["能力包 {meta.name}"]')
    lines.append(
        f'        meta_attr["kind={meta.kind} · display={display_name_for_kind(meta.kind)}'
        f'<br/>affinity: {affinity_str}"]'
    )
    lines.append("    end")

    # Budget footer (as a comment so it doesn't add visual weight).
    if include_budget and (meta.budget_tokens or meta.budget_usd):
        lines.append(
            f"    %% budget: {meta.budget_tokens:,} tokens, "
            f"${meta.budget_usd:.2f}, {meta.budget_latency_ms // 1000}s"
        )

    return "\n".join(lines) + "\n"
