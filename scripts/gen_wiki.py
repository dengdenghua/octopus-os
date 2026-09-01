"""Implementation note."""
from __future__ import annotations

import argparse
import ast
import datetime as _dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "auto"


# ═══════════════════════════════════════════════════════════
# AST helpers
# ═══════════════════════════════════════════════════════════


def _module_docstring(path: Path) -> str:
    """Return first paragraph of the module docstring · or empty."""
    try:
        src = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    try:
        doc = ast.get_docstring(ast.parse(src))
    except SyntaxError:
        return ""
    if not doc:
        return ""
    # First paragraph
    for para in doc.split("\n\n"):
        para = para.strip()
        if para:
            # collapse whitespace
            return re.sub(r"\s+", " ", para)[:300]
    return ""


def _public_symbols(path: Path) -> list[str]:
    """Return module-level ``__all__`` or top-level public defs."""
    try:
        src = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    # Prefer __all__
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                try:
                    val = ast.literal_eval(node.value)
                    if isinstance(val, (list, tuple)):
                        return [str(x) for x in val if isinstance(x, str)]
                except (ValueError, TypeError):
                    pass
    # Fallback: top-level non-_ classes / funcs
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
            names.append(node.name)
    return names


def _load_jsonc(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    cleaned: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("//"):
            continue
        # Strip trailing ``//`` line comments — but only when the ``//``
        # is OUTSIDE any string literal. Naïvely searching for ``//``
        # corrupts URLs (``"https://..."``) and any other string that
        # legitimately contains a double slash. Walk the line tracking
        # quote state and only honour ``//`` while not inside quotes.
        in_str = False
        escape = False
        comment_at: int | None = None
        for i, ch in enumerate(line):
            if escape:
                escape = False
                continue
            if ch == "\\" and in_str:
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if not in_str and ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                comment_at = i
                break
        if comment_at is not None:
            head = line[:comment_at].rstrip()
            if head.endswith((",", "{", "[", ":")):
                line = head
        cleaned.append(line)
    return json.loads("\n".join(cleaned))


# ═══════════════════════════════════════════════════════════
# TOC node types
# ═══════════════════════════════════════════════════════════


@dataclass
class DocNode:
    # String forward-reference to self works under normal import but
    # breaks when the module is loaded via ``importlib.util``
    # (dataclass resolver looks up ``sys.modules[__module__]`` and
    # gets None for dynamically-loaded modules). Use ``list[Any]``
    # to sidestep · runtime shape is identical.
    type: str  # "doc" | "dir"
    title: str
    path: str = ""  # for doc
    children: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "title": self.title}
        if self.type == "doc":
            d["path"] = self.path
        else:
            d["children"] = [c.to_dict() for c in self.children]
        return d


# ═══════════════════════════════════════════════════════════
# Shared data sources · cached
# ═══════════════════════════════════════════════════════════


def _load_catalog() -> dict[str, dict[str, Any]]:
    src = (ROOT / "runtime/execution/all_skills/__init__.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "_CATALOG" and node.value is not None:
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_CATALOG":
                    return ast.literal_eval(node.value)
    raise RuntimeError("_CATALOG dict not found in all_skills/__init__.py")


def _load_arm_skill_map() -> dict[str, list[str]]:
    """Parse runtime/execution/arms/presets.py for arm → skills."""
    src = (ROOT / "runtime/execution/arms/presets.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(src)
    group_lists: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and tgt.id.startswith("_") and tgt.id.isupper() and isinstance(node.value, ast.List):
                skills: list[str] = []
                for elt in node.value.elts:
                    if (
                        isinstance(elt, ast.Call)
                        and elt.args
                        and isinstance(elt.args[0], ast.Constant)
                        and isinstance(elt.args[0].value, str)
                    ):
                        skills.append(elt.args[0].value)
                if skills:
                    group_lists[tgt.id] = skills
    arm_to_skills: dict[str, list[str]] = {}
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("make_") and node.name.endswith("_arm")):
            continue
        arm_id = node.name[len("make_"):-len("_arm")]
        skills: list[str] = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                for kw in sub.keywords:
                    if kw.arg == "arm_id" and isinstance(kw.value, ast.Call) and kw.value.args:
                        a0 = kw.value.args[0]
                        if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                            arm_id = a0.value
                    if kw.arg == "allowed_skills" and isinstance(kw.value, ast.List):
                        for elt in kw.value.elts:
                            if isinstance(elt, ast.Starred) and isinstance(elt.value, ast.Name):
                                skills.extend(group_lists.get(elt.value.id, []))
                            elif (
                                isinstance(elt, ast.Call)
                                and elt.args
                                and isinstance(elt.args[0], ast.Constant)
                                and isinstance(elt.args[0].value, str)
                            ):
                                skills.append(elt.args[0].value)
        if skills:
            arm_to_skills[arm_id.removesuffix("_arm")] = sorted(set(skills))
    return arm_to_skills


def _load_agents() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for agent_dir in sorted((ROOT / "agents").iterdir()):
        if not agent_dir.is_dir() or agent_dir.name.startswith("_"):
            continue
        profile_path = agent_dir / "profile.jsonc"
        tools_path = agent_dir / "agent-core" / "tool-registry.jsonc"
        if not profile_path.exists():
            continue
        profile = _load_jsonc(profile_path)
        tools = _load_jsonc(tools_path) if tools_path.exists() else {}
        out[profile.get("id", agent_dir.name)] = {
            "name": profile.get("name", agent_dir.name),
            "icon": profile.get("icon", ""),
            "description": profile.get("description", ""),
            "arms": list(tools.get("arms", [])),
            "extra_affinity": list(tools.get("extra_affinity", [])),
            "capabilities": dict(profile.get("capabilities") or {}),
            "dir": agent_dir.name,
        }
    return out


# ═══════════════════════════════════════════════════════════
# Cross-reference scan · who imports what
# ═══════════════════════════════════════════════════════════


def _build_import_graph() -> dict[str, set[str]]:
    """Walk ``runtime/`` and index every ``from runtime.X.Y import ...``
    or ``import runtime.X.Y`` statement. Returns ``{target_subpkg:
    {importing_file_rel}}`` · where ``target_subpkg`` is the 2-segment
    dotted path like ``runtime.execution.tool_engine`` and importing files
    are ``runtime/...py`` POSIX paths.

    Pre-computed once at start of ``generate_all`` · used by
    ``_describe_dir`` to add "Who imports this" sections.
    """
    graph: dict[str, set[str]] = {}
    runtime_dir = ROOT / "runtime"
    for py in runtime_dir.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(ROOT).as_posix()
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        # Self-module dotted name · skip self-references in results
        self_mod = (
            "runtime" + "." + py.relative_to(runtime_dir).with_suffix("").as_posix().replace("/", ".")
        )
        for node in ast.walk(tree):
            target: str | None = None
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("runtime."):
                    target = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("runtime."):
                        target = alias.name
                        break
            if target and target != self_mod:
                # Index at 2-segment (sub-package) granularity so
                # ``runtime.execution.tool_engine.executor`` and
                # ``runtime.execution.tool_engine`` both attribute to the
                # package ``runtime.execution.tool_engine``.
                parts = target.split(".")
                key = ".".join(parts[:3]) if len(parts) >= 3 else target
                graph.setdefault(key, set()).add(rel)
    return graph


def _importers_of(pkg: Path, graph: dict[str, set[str]]) -> list[str]:
    """Given a runtime sub-package path, return sorted POSIX file
    paths that import anything from it. Excludes files INSIDE the
    package (self-references within beak/ to beak/ aren't interesting
    for the "who uses me" view)."""
    rel = pkg.relative_to(ROOT).as_posix().replace("/", ".")
    importers = graph.get(rel, set())
    pkg_prefix = pkg.relative_to(ROOT).as_posix() + "/"
    result = [
        f for f in importers
        if not f.startswith(pkg_prefix) and f != pkg.relative_to(ROOT).as_posix()
    ]
    return sorted(result)


# ═══════════════════════════════════════════════════════════
# Module-page generators · produce (title, markdown)
# ═══════════════════════════════════════════════════════════


def _describe_dir(
    pkg: Path,
    *,
    dir_title: str,
    prelude: str,
    import_graph: dict[str, set[str]] | None = None,
) -> str:
    """Scan a runtime sub-package · emit one module page."""
    lines: list[str] = [
        f"# {dir_title}",
        "",
        f"> {prelude}",
        "",
        f"**Source**: `{pkg.relative_to(ROOT).as_posix()}/`",
        "",
    ]
    files = sorted(
        p for p in pkg.rglob("*.py")
        if "__pycache__" not in p.parts and p.name != "__init__.py"
    )
    init = pkg / "__init__.py"
    if init.exists():
        doc = _module_docstring(init)
        if doc:
            lines.append("## Package summary")
            lines.append("")
            lines.append(doc)
            lines.append("")
        exports = _public_symbols(init)
        if exports:
            lines.append("## Exports")
            lines.append("")
            for s in exports:
                lines.append(f"- `{s}`")
            lines.append("")
    if files:
        lines.append("## Modules")
        lines.append("")
        lines.append("| Module | Summary |")
        lines.append("| --- | --- |")
        for f in files:
            rel = f.relative_to(pkg).as_posix()
            doc = _module_docstring(f) or "—"
            doc = doc.replace("|", "\\|")
            lines.append(f"| `{rel}` | {doc} |")
        lines.append("")

    # ── Reverse-import map · "who uses this package" ───────
    if import_graph is not None:
        importers = _importers_of(pkg, import_graph)
        if importers:
            lines.append("## Who imports this")
            lines.append("")
            lines.append(f"**{len(importers)}** file(s) reference this package:")
            lines.append("")
            # Group by top-level area to avoid a wall of paths
            groups: dict[str, list[str]] = {}
            for f in importers:
                area = f.split("/")[1] if f.startswith("runtime/") else f.split("/")[0]
                groups.setdefault(area, []).append(f)
            for area in sorted(groups):
                lines.append(f"- **`runtime/{area}/`** · {len(groups[area])} file(s)")
                # Collapse if > 6 files in one area, else list
                if len(groups[area]) <= 6:
                    for f in sorted(groups[area]):
                        lines.append(f"  - `{f}`")
                else:
                    for f in sorted(groups[area])[:5]:
                        lines.append(f"  - `{f}`")
                    lines.append(f"  - _… and {len(groups[area]) - 5} more_")
            lines.append("")
    return "\n".join(lines) + "\n"


def page_overview() -> str:
    """Top-level repo overview · pulls from README + docs/architecture.md."""
    pieces: list[str] = [
        "# 项目概述 · Project Overview",
        "",
        "> 自动从仓库结构提取。Echo · The Open-Source Multi-Agent AI Workspace.",
        "",
    ]
    readme = ROOT / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        # Take the first non-empty paragraph after the first H1
        paras = re.split(r"\n\s*\n", text)
        for para in paras:
            if para.strip().startswith("#") or not para.strip():
                continue
            # skip HTML blocks
            if para.lstrip().startswith("<"):
                continue
            pieces.append(para.strip()[:800])
            pieces.append("")
            break

    # Directory inventory at the top level.
    pieces.append("## 仓库结构")
    pieces.append("")
    top_dirs = [
        ("runtime/", "Python runtime (agents / planner / executor / safety / memory)"),
        ("frontend/", "React + Vite SPA for the webui"),
        ("agents/", "Per-agent profile + memory + workspace directories"),
        ("docs/", "Human-written architecture docs, ADRs, invariants"),
        ("docs/auto/", "← you are here · auto-generated"),
        ("tests/", "Pytest suite (backend)"),
        ("scripts/", "Tooling (this generator + OpenAPI snapshot)"),
        ("protocols/", "8 protocol specs (digestion / immunity / swarm / …)"),
    ]
    pieces.append("| Directory | Purpose |")
    pieces.append("| --- | --- |")
    for d, purpose in top_dirs:
        pieces.append(f"| `{d}` | {purpose} |")
    pieces.append("")

    # Stats
    pieces.append("## 规模")
    pieces.append("")
    py_files = sum(
        1 for _ in (ROOT / "runtime").rglob("*.py") if "__pycache__" not in _.parts
    )
    tsx_files = sum(
        1 for _ in (ROOT / "frontend/src").rglob("*.tsx")
    )
    test_files = sum(1 for _ in (ROOT / "tests").rglob("test_*.py"))
    pieces.append(f"- Python 模块：**{py_files}** 个（runtime/）")
    pieces.append(f"- TSX 组件：**{tsx_files}** 个（frontend/src）")
    pieces.append(f"- 后端测试：**{test_files}** 个")
    pieces.append("")
    return "\n".join(pieces) + "\n"


def page_tech_stack() -> str:
    lines: list[str] = ["# 技术栈 · Tech Stack", "", "> 从 `pyproject.toml` + `frontend/package.json` 抽取的关键依赖。", ""]

    # Python deps
    pyproject = ROOT / "pyproject.toml"
    py_deps: list[str] = []
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8")
        m = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL)
        if m:
            for line in m.group(1).splitlines():
                line = line.strip().strip(",").strip('"').strip("'")
                if line and not line.startswith("#"):
                    py_deps.append(line)
    lines.append("## 后端（Python）")
    lines.append("")
    if py_deps:
        for d in sorted(py_deps)[:25]:
            lines.append(f"- `{d}`")
        if len(py_deps) > 25:
            lines.append(f"- … 共 {len(py_deps)} 个依赖")
    else:
        lines.append("_未能从 pyproject.toml 解析依赖_")
    lines.append("")

    # JS deps
    pkg_json = ROOT / "frontend/package.json"
    js_deps: list[str] = []
    if pkg_json.exists():
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
        js_deps.extend(sorted(data.get("dependencies", {}).keys()))
    lines.append("## 前端（React / Vite）")
    lines.append("")
    if js_deps:
        for d in js_deps[:25]:
            lines.append(f"- `{d}`")
        if len(js_deps) > 25:
            lines.append(f"- … 共 {len(js_deps)} 个依赖")
    else:
        lines.append("_未能从 package.json 解析依赖_")
    lines.append("")
    return "\n".join(lines) + "\n"


def page_backend_index(import_graph: dict[str, set[str]]) -> str:
    """Backend overview + mermaid dep graph · `sub-package → top 3
    inbound areas` gives a one-glance feel for the shape."""
    lines: list[str] = [
        "# 后端架构 · Backend",
        "",
        "> Python runtime · 分 6 个子系统 · 左侧树展开看每个子系统详情。",
        "",
        "| 子系统 | 目录 | 职责 |",
        "| --- | --- | --- |",
        "| Runtime 核心 | `runtime/execution/`, `runtime/core/` | 执行器 · 规划 · 技能注册 · 心跳 |",
        "| Safety | `runtime/safety/` | 宪法 · 免疫 · 生命周期 hooks |",
        "| Memory | `runtime/memory/` | Journal (genome) · Context (hemolymph) |",
        "| Sensing | `runtime/sensing/` | Eyes (model router) · Siphon (HTTP API) |",
        "| Adapters | `runtime/adapters/` | MCP · Channels · 第三方集成 |",
        "| Agents | `agents/` | 预置 agent 的 profile / memory / workspace |",
        "",
        "## 依赖关系（自动计算）",
        "",
        "每个子系统被**多少**子系统引用 · 静态 AST 扫描 ``from runtime.X ...`` 语句得出。",
        "前端 Wiki 面板会把下面的 ```mermaid``` 渲染成真图。",
        "",
    ]

    # Aggregate: for each sub-package (2-segment path under runtime),
    # count importers grouped by THEIR top-level area.
    areas = [
        ("runtime.execution", "execution"),
        ("runtime.core", "core"),
        ("runtime.safety", "safety"),
        ("runtime.memory", "memory"),
        ("runtime.sensing", "sensing"),
        ("runtime.adapters", "adapters"),
        ("runtime.platform", "platform"),
    ]
    # pair(from → to) → count
    edges: dict[tuple[str, str], int] = {}
    for target_key, importers in import_graph.items():
        # Normalize target to its top-level area under runtime
        parts = target_key.split(".")
        if len(parts) < 2:
            continue
        to_area = parts[1]
        for f in importers:
            segs = f.split("/")
            if len(segs) >= 2 and segs[0] == "runtime":
                from_area = segs[1]
                if from_area == to_area:
                    continue  # skip self-loops
                edges[(from_area, to_area)] = edges.get((from_area, to_area), 0) + 1

    # Only keep edges with ≥ 3 imports · cuts noise from stray refs.
    # Stable tiebreakers (from, to) so output is deterministic across
    # runs · rglob order can vary and Python's stable sort would
    # otherwise leak dict-insertion order into the markdown.
    strong_edges = sorted(
        ((f, t, c) for (f, t), c in edges.items() if c >= 3),
        key=lambda e: (-e[2], e[0], e[1]),
    )

    lines.append("```mermaid")
    lines.append("graph LR")
    # Declare nodes so the graph still renders even if an area
    # happens to have no strong edges this run.
    for _key, area in areas:
        lines.append(f"  {area}[{area}]")
    for f, t, c in strong_edges:
        lines.append(f"  {f} -- {c} --> {t}")
    lines.append("```")
    lines.append("")
    return "\n".join(lines) + "\n"


def page_agent(agent_id: str, meta: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# {meta.get('icon', '')} {meta['name']} · `{agent_id}`",
        "",
        f"> {meta['description'] or '_no description_'}",
        "",
        f"**Agent dir**: `agents/{meta['dir']}/`",
        "",
    ]
    if meta["arms"]:
        lines.append("## Arms（外显能力）")
        lines.append("")
        for arm in meta["arms"]:
            lines.append(f"- `{arm}`")
        lines.append("")
    if meta["capabilities"]:
        lines.append("## Capabilities（能力 flags）")
        lines.append("")
        for k, v in meta["capabilities"].items():
            checkmark = "✅" if v else "❌"
            lines.append(f"- {checkmark} `{k}`")
        lines.append("")
    if meta["extra_affinity"]:
        lines.append("## Affinity keywords（路由亲和度）")
        lines.append("")
        lines.append(", ".join(f"`{a}`" for a in meta["extra_affinity"]))
        lines.append("")
    # Soul / identity snippet
    core_dir = ROOT / "agents" / meta["dir"] / "agent-core"
    for name in ("SOUL.md", "IDENTITY.md"):
        p = core_dir / name
        if p.exists():
            text = p.read_text(encoding="utf-8").strip()
            if text:
                lines.append(f"## {name}")
                lines.append("")
                # Short preview · first 500 chars
                lines.append(text[:500] + ("…" if len(text) > 500 else ""))
                lines.append("")
    return "\n".join(lines) + "\n"


def page_skill_map(catalog: dict[str, dict[str, Any]], arm_to_skills: dict[str, list[str]], agents: dict[str, dict[str, Any]]) -> str:
    """Flat catalog table (the old skill-map.md content · preserved)."""
    skill_to_arms: dict[str, list[str]] = {}
    for arm, skills in arm_to_skills.items():
        for s in skills:
            skill_to_arms.setdefault(s, []).append(arm)
    arm_to_agents: dict[str, list[str]] = {}
    for agent_id, meta in agents.items():
        for arm in meta["arms"]:
            arm_to_agents.setdefault(arm, []).append(agent_id)

    lines = [
        "# Skills × Arms × Agents",
        "",
        "> Ground truth for *which agent can invoke which skill* · "
        "derived from `_CATALOG` (all_skills) + `make_*_arm` (presets) + "
        "每个 agent 的 `tool-registry.jsonc`。",
        "",
        "## Skills catalog",
        "",
        "| Skill | Group | Atomic | In arms | Used by agents |",
        "| --- | --- | --- | --- | --- |",
    ]
    for skill_id in sorted(catalog):
        meta = catalog[skill_id]
        group = meta.get("group", "-")
        atomic = "✅" if meta.get("atomic") else ""
        arms = skill_to_arms.get(skill_id, [])
        ag_set: set[str] = set()
        for a in arms:
            ag_set.update(arm_to_agents.get(a, []))
        if meta.get("atomic"):
            ag_set.update(agents.keys())
        arms_cell = ", ".join(sorted(arms)) or "—"
        ag_cell = ", ".join(sorted(ag_set)) or "—"
        lines.append(
            f"| `{skill_id}` | {group} | {atomic} | {arms_cell} | {ag_cell} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def page_hook_surface() -> str:
    runtime_dir = ROOT / "runtime"
    dispatch_re = re.compile(
        r"\bdispatch_(?P<event>pre_tool|post_tool|user_prompt|stop|session_start|notification)\b",
    )
    sites: dict[str, list[tuple[str, int]]] = {}
    for py in runtime_dir.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        if py.resolve() == (ROOT / "runtime/safety/hooks/runner.py").resolve():
            continue
        if py.resolve() == (ROOT / "runtime/safety/hooks/__init__.py").resolve():
            continue
        rel = py.relative_to(ROOT).as_posix()
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            m = dispatch_re.search(line)
            if m and "from runtime.safety.hooks" not in line and "import " not in line:
                sites.setdefault(m.group("event"), []).append((rel, i))

    lines = ["# Hook surface", "", "> 每个 lifecycle-hook 的 dispatch 调用点 · 社区 handler 通过 `@register_hook(EventType)` 订阅。", ""]
    for event in sorted(sites):
        entries = sorted(set(sites[event]))
        lines.append(f"## `{event}` · {len(entries)} 处")
        lines.append("")
        for rel, line in entries:
            lines.append(f"- `{rel}:{line}`")
        lines.append("")
    all_events = {"pre_tool", "post_tool", "user_prompt", "stop", "session_start", "notification"}
    missing = all_events - set(sites)
    if missing:
        lines.append("## Defined but never dispatched")
        lines.append("")
        for ev in sorted(missing):
            lines.append(f"- `{ev}`")
        lines.append("")
    return "\n".join(lines) + "\n"


def page_adr_anchors() -> str:
    adr_dir = ROOT / "docs" / "adr"
    code_re = re.compile(
        r"`(?P<path>(?:runtime|tests|frontend/src|docs|scripts|agents|protocols)/[^`\s]+?)`"
    )
    entries: list[tuple[str, str, str, set[str]]] = []
    for md in sorted(adr_dir.glob("*.md")):
        if md.name == "README.md":
            continue
        text = md.read_text(encoding="utf-8")
        tm = re.search(r"^#\s+(.*)$", text, re.MULTILINE)
        title = tm.group(1).strip() if tm else md.stem
        sm = re.search(r"^Status:\s*([A-Za-z ]+)", text, re.MULTILINE)
        status = sm.group(1).strip() if sm else "?"
        refs: set[str] = set()
        for m in code_re.finditer(text):
            refs.add(m.group("path").rstrip(".,;:)"))
        entries.append((md.name, title, status, refs))

    file_to_adrs: dict[str, list[str]] = {}
    for name, _title, _status, refs in entries:
        for ref in refs:
            file_to_adrs.setdefault(ref, []).append(name)

    lines = [
        "# ADR 锚点 · Governance Map",
        "",
        "> 哪个 ADR 治理哪段代码 · 从 ADR markdown 里的反引号路径引用抽取 · "
        "**PR 审查时用**：改了某文件 · 看它被哪些 ADR 引用。",
        "",
        "## Per ADR",
        "",
    ]
    for name, title, status, refs in entries:
        lines.append(f"### [{title}](../../adr/{name}) · *{status}*")
        lines.append("")
        if refs:
            for ref in sorted(refs):
                lines.append(f"- `{ref}`")
        else:
            lines.append("_未引用代码路径_")
        lines.append("")
    lines.append("## Per file")
    lines.append("")
    for path in sorted(file_to_adrs):
        adrs = sorted(set(file_to_adrs[path]))
        adr_cell = ", ".join(
            f"[{a.removesuffix('.md')}](../../adr/{a})" for a in adrs
        )
        lines.append(f"- `{path}` ← {adr_cell}")
    lines.append("")
    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════
# Main assembly · produces dict {relpath: markdown}
# ═══════════════════════════════════════════════════════════


def generate_all() -> tuple[dict[str, str], DocNode]:
    catalog = _load_catalog()
    arm_to_skills = _load_arm_skill_map()
    agents = _load_agents()
    import_graph = _build_import_graph()

    out: dict[str, str] = {}

    # ── Level 0 · index ────────────────────────────────────
    out["00-overview.md"] = page_overview()
    out["10-tech-stack.md"] = page_tech_stack()

    # ── Backend architecture ───────────────────────────────
    out["20-backend/index.md"] = page_backend_index(import_graph)

    runtime_subs: list[tuple[str, str, str, str]] = [
        ("21-runtime/tool-engine.md", "Tool Engine · 执行器", "runtime/execution/tool_engine",
         "把每步 tool call 串起整套治理 · Auth / Budget / Journal / Hooks · 同时做 OTel span。"),
        ("21-runtime/cerebrum.md", "Cerebrum · 规划器", "runtime/core/cerebrum",
         "LLM Planner + Static Planner · 把自然语言意图拆成 TaskGraph。"),
        ("21-runtime/suckers.md", "Suckers · 技能注册", "runtime/execution/suckers",
         "Skill 注册表 · 原子层 · 沙箱 · 测试 tier。"),
        ("21-runtime/arms.md", "Arms · 执行工具组", "runtime/execution/arms",
         "Arm preset 工厂 · 将原始 Skill 按职责打包成 arm（fs_writer / git / shell / browser_read / ...）。"),
        ("21-runtime/all-skills.md", "All Skills 目录", "runtime/execution/all_skills",
         "全仓 Skill 目录 · 每个 skill 分 group / atomic 标记 · 决定哪个 arm 能包含它。"),
        ("22-safety/validation.md", "Safety · Validation", "runtime/safety/validation",
         "宪法层 · PRIV/LAWF/DGNT/SELF/EXFIL 五类 · rule gate + LLM judge + profile 降级。"),
        ("22-safety/auth.md", "Safety · Auth", "runtime/safety/auth",
         "TrustEngine · allow/quarantine/reject · IMM-I1~I6 不变量守护。"),
        ("22-safety/hooks.md", "Safety · Hooks", "runtime/safety/hooks",
         "Tool lifecycle hooks · 6 个事件 · sync + async handler · ESLint rules-of-hooks=error 静态守护。"),
        ("22-safety/recovery.md", "Safety · Recovery", "runtime/safety/recovery",
         "MemoryConsolidator · SkillForge · KG updater · 从 trajectory 反哺记忆 / 技能 / 图谱。"),
        ("23-memory/journal.md", "Memory · Journal", "runtime/memory/journal",
         "全 append-only 日志 · events: trajectory / immune / budget / step · 所有 agent 行为的 ground truth。"),
        ("23-memory/hemolymph.md", "Memory · Hemolymph (Context)", "runtime/memory/hemolymph",
         "Context Composer · 给 planner 组装上下文（最近 trajectory + learned rules + memories）。"),
        ("24-sensing/model-router.md", "Sensing · Model Router", "runtime/sensing/model_router",
         "ModelRouter 抽象 · Anthropic / OpenAI / Gemini / Ollama / Mock / MultiModelRouter (multi-provider fallback)。"),
        ("24-sensing/gateway.md", "Sensing · Gateway (HTTP API)", "runtime/sensing/gateway",
         "全部 FastAPI router · openai_gateway / meta / mcp / config / channels / thread_compat / …"),
        ("25-adapters/mcp.md", "Adapters · MCP", "runtime/adapters/mcp_client",
         "MCP 客户端 + Trust store · ADR-007 治理 · 未审批 server 的工具拒注册。"),
        ("25-adapters/channels.md", "Adapters · Channels", "runtime/adapters/channels",
         "外部 channel adapter (Slack / Discord / 微信 / …) · 必须走 validation safe_send 才允许出站。"),
        ("25-adapters/integrations.md", "Adapters · Integrations", "runtime/adapters/integrations",
         "Local auth 路由 · 各家第三方集成的 router proxy。"),
    ]
    for rel, title, pkg_rel, prelude in runtime_subs:
        pkg = ROOT / pkg_rel
        if pkg.exists():
            out[f"20-backend/{rel}"] = _describe_dir(
                pkg, dir_title=title, prelude=prelude,
                import_graph=import_graph,
            )

    # Agents
    for agent_id, meta in sorted(agents.items()):
        out[f"20-backend/26-agents/{agent_id}.md"] = page_agent(agent_id, meta)

    # ── Graphs · skill / hook / ADR ────────────────────────
    out["30-skills-graph/skill-map.md"] = page_skill_map(catalog, arm_to_skills, agents)
    out["40-hooks/hook-surface.md"] = page_hook_surface()
    out["50-governance/adr-anchors.md"] = page_adr_anchors()

    # ── Build TOC tree ─────────────────────────────────────
    tree = DocNode(type="dir", title="root", children=[
        DocNode(type="doc", title="项目概述", path="00-overview.md"),
        DocNode(type="doc", title="技术栈", path="10-tech-stack.md"),
        DocNode(type="dir", title="后端架构", children=[
            DocNode(type="doc", title="概览", path="20-backend/index.md"),
            DocNode(type="dir", title="Runtime 核心", children=[
                DocNode(type="doc", title="Tool Engine · 执行器", path="20-backend/21-runtime/tool-engine.md"),
                DocNode(type="doc", title="Cerebrum · 规划", path="20-backend/21-runtime/cerebrum.md"),
                DocNode(type="doc", title="Suckers · 技能", path="20-backend/21-runtime/suckers.md"),
                DocNode(type="doc", title="Arms · 工具组", path="20-backend/21-runtime/arms.md"),
                DocNode(type="doc", title="All Skills 目录", path="20-backend/21-runtime/all-skills.md"),
            ]),
            DocNode(type="dir", title="Safety", children=[
                DocNode(type="doc", title="Validation", path="20-backend/22-safety/validation.md"),
                DocNode(type="doc", title="Auth", path="20-backend/22-safety/auth.md"),
                DocNode(type="doc", title="Hooks", path="20-backend/22-safety/hooks.md"),
                DocNode(type="doc", title="Recovery", path="20-backend/22-safety/recovery.md"),
            ]),
            DocNode(type="dir", title="Memory", children=[
                DocNode(type="doc", title="Journal", path="20-backend/23-memory/journal.md"),
                DocNode(type="doc", title="Hemolymph (Context)", path="20-backend/23-memory/hemolymph.md"),
            ]),
            DocNode(type="dir", title="Sensing", children=[
                DocNode(type="doc", title="Model Router", path="20-backend/24-sensing/model-router.md"),
                DocNode(type="doc", title="Gateway · HTTP API", path="20-backend/24-sensing/gateway.md"),
            ]),
            DocNode(type="dir", title="Adapters", children=[
                DocNode(type="doc", title="MCP", path="20-backend/25-adapters/mcp.md"),
                DocNode(type="doc", title="Channels", path="20-backend/25-adapters/channels.md"),
                DocNode(type="doc", title="Integrations", path="20-backend/25-adapters/integrations.md"),
            ]),
            DocNode(type="dir", title="Agents", children=[
                DocNode(type="doc", title=f"{m.get('icon','')} {m['name']}", path=f"20-backend/26-agents/{aid}.md")
                for aid, m in sorted(agents.items())
            ]),
        ]),
        DocNode(type="dir", title="图谱 · Graphs", children=[
            DocNode(type="doc", title="Skill × Arm × Agent", path="30-skills-graph/skill-map.md"),
            DocNode(type="doc", title="Hook Surface", path="40-hooks/hook-surface.md"),
            DocNode(type="doc", title="ADR 锚点", path="50-governance/adr-anchors.md"),
        ]),
    ])

    # ── Index README ───────────────────────────────────────
    readme_lines = [
        "# Auto-generated wiki",
        "",
        "<!-- Regenerate: `python scripts/gen_wiki.py` -->",
        "",
        "仓库级自动生成文档 · 代码每次改动重跑 `scripts/gen_wiki.py` · "
        "CI gate 守护 (`tests/test_auto_docs_fresh.py`) 防 drift。",
        "",
        "## 生成范围",
        "",
        f"- **{sum(1 for p in out if p.endswith('.md'))}** 个页面（不含本 index）",
        f"- 覆盖 **{len(agents)}** 个预置 agent",
        f"- 覆盖 **{len(catalog)}** 个 skill × **{len(arm_to_skills)}** 个 arm",
        "",
        "## 入口",
        "",
        "- [项目概述](00-overview.md)",
        "- [技术栈](10-tech-stack.md)",
        "- [后端架构](20-backend/index.md)",
        "- [Skill × Arm × Agent 图谱](30-skills-graph/skill-map.md)",
        "- [Hook Surface](40-hooks/hook-surface.md)",
        "- [ADR 锚点](50-governance/adr-anchors.md)",
        "",
    ]
    out["README.md"] = "\n".join(readme_lines) + "\n"

    # ── index.json manifest ────────────────────────────────
    manifest = {
        "version": 2,
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "files_analyzed": len(out),
        "tree": [c.to_dict() for c in tree.children],
    }
    out["index.json"] = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"

    return out, tree


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit 1 on drift")
    args = ap.parse_args()

    outputs, _ = generate_all()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Remove old files not produced this run (keeps the tree clean when
    # a module is renamed or dropped).
    existing: set[Path] = set()
    if OUT_DIR.exists():
        for p in OUT_DIR.rglob("*"):
            if p.is_file():
                existing.add(p)
    desired: set[Path] = {OUT_DIR / rel for rel in outputs}
    stale = existing - desired

    drift = False
    for rel, content in outputs.items():
        path = OUT_DIR / rel
        current = path.read_text(encoding="utf-8") if path.exists() else None
        matches = current == content
        if args.check and rel == "index.json" and current is not None:
            # generated_at records the real generation time. Ignore only that
            # volatile field while continuing to verify the manifest payload.
            try:
                current_manifest = json.loads(current)
                expected_manifest = json.loads(content)
                current_manifest.pop("generated_at", None)
                expected_manifest.pop("generated_at", None)
                matches = current_manifest == expected_manifest
            except json.JSONDecodeError:
                matches = False
        if not matches:
            drift = True
            if args.check:
                print(f"[drift] {path.relative_to(ROOT)}", file=sys.stderr)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                print(f"[write] {path.relative_to(ROOT)}")

    for p in stale:
        drift = True
        if args.check:
            print(f"[stale] {p.relative_to(ROOT)}", file=sys.stderr)
        else:
            p.unlink()
            print(f"[rm] {p.relative_to(ROOT)}")

    # Clean empty dirs (post delete).
    if not args.check:
        for p in sorted(OUT_DIR.rglob("*"), reverse=True):
            if p.is_dir() and not any(p.iterdir()):
                p.rmdir()

    if args.check and drift:
        print(
            "\nWiki out of date · run `python scripts/gen_wiki.py`.",
            file=sys.stderr,
        )
        return 1
    if not drift and not args.check:
        print("[ok] all wiki pages already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
