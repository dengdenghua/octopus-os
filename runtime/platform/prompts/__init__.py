# ruff: noqa: E402 — module-level imports below are intentionally late

from __future__ import annotations

import logging
import os
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency
    yaml = None

_logger = logging.getLogger(__name__)

_BUILTIN: dict[str, str] = {}


def _register_builtin(name: str, text: str) -> None:
    _BUILTIN[name] = text


def _init_builtins() -> None:
    _register_builtin(
        "planner_base",
        """You are the planning module of an agent framework.

Given a user goal and a list of available skills, produce a JSON plan.

REQUIRED OUTPUT FORMAT (strict):
```json
{
  "reasoning": "one-sentence why",
  "nodes": [
    {"skill": "<skill_name>", "args": {"<key>": "<value>"}, "depends_on": [0]}
  ]
}
```

RULES:
- Only use skills from the provided AVAILABLE SKILLS list.
- args values are strings or JSON literals.
- Use "{nX.key}" templates to reference prior nodes' outputs (e.g. {n0.path}).
- "depends_on" is OPTIONAL — a list of prior node indices (or "nN" strings)
  that MUST complete before this node runs. Omit or leave empty for nodes
  that can run in parallel with their siblings. The runtime also infers
  dependencies from "{nX.key}" template references in args, so omitting
  depends_on is safe when data flow is already encoded in templates.
- Keep plans short; 1-5 nodes is ideal.
- Do NOT include prose before or after the JSON.
""",
    )
    _register_builtin(
        "prompt_mutator",
        """\
You are a prompt engineer. Your job is to improve a planning agent's
system-prompt suffix based on evidence of failures.

You will see:
    - the current suffix (may be empty)
    - recent failed trajectories with each step's skill, args, error type

Return ONLY the improved suffix wrapped in <suffix>...</suffix> tags. Keep the
suffix concise (under 400 characters). Focus on concrete, actionable guidance
grounded in the observed failures (not generic advice).

Examples of good suffixes:
    "When read_file returns empty, try file_stats first to check size."
    "Avoid plans longer than 3 steps unless explicitly required."

You may precede the <suffix> with a one-line <reason>...</reason>.
Do NOT restate the user task. Do NOT speculate about unseen failure modes.
""",
    )
    _register_builtin(
        "prompt_merge",
        """\
You are a prompt engineer. You see two system-prompt suffixes that both
performed well on different trajectories. Your job is to synthesize a SINGLE
new suffix that combines the strengths of both while avoiding redundancy.

Return ONLY the merged suffix wrapped in <suffix>...</suffix> tags. Keep it
under 500 characters. Do NOT just concatenate the two inputs verbatim.
You may precede the <suffix> with a one-line <reason>...</reason>.
""",
    )
    _register_builtin(
        "agent_reminder",
        "Use the active agent identity from the current system prompt. "
        "If asked who you are, answer with that agent's display name, "
        "not the product/runtime name unless the agent itself is named Echo.",
    )
    _register_builtin(
        "computer_use_planner",
        """You are a desktop automation planner. You see screenshots and user goals.
Plan step-by-step actions to achieve the goal.
Output JSON with action sequences.
""",
    )


_init_builtins()


def _default_search_dirs() -> list[Path]:
    """Where to look for prompt YAML when the caller did not say.

    The seven prompts under ``prompts/`` are tracked repo assets, so they must
    resolve no matter what the working directory is. A bare ``Path("prompts")``
    only worked when the process happened to be started from the repo root:
    anything that changed directory — a server launched elsewhere, or a test
    that chdir'd — got ``KeyError: Prompt '...' not found`` for a file that was
    sitting right there.

    The cwd-relative directory is kept FIRST so a project-local ``prompts/``
    override still wins; the resolved repo copy is the fallback.
    """
    dirs = [Path("prompts")]
    try:
        from runtime.platform.process.paths import resources_root

        bundled = resources_root() / "prompts"
    except (ImportError, OSError):  # pragma: no cover — never break prompt load
        return dirs
    if bundled not in dirs:
        dirs.append(bundled)
    return dirs


class PromptLoader:
    def __init__(
        self,
        search_dirs: list[Path | str] | None = None,
    ) -> None:
        """
        Parameters
        ----------
        search_dirs :
            Directories to search for YAML prompt files (in order).
            Defaults to ["prompts/"] relative to cwd.
        """
        if search_dirs is None:
            search_dirs = _default_search_dirs()
        self._search_dirs = [Path(d) for d in search_dirs]
        self._cache: dict[str, str] = {}
        self._file_map: dict[str, Path] = {}

    def _find_file(self, name: str) -> Path | None:
        for d in self._search_dirs:
            for ext in (".yaml", ".yml"):
                p = d / f"{name}{ext}"
                if p.exists():
                    return p
        return None

    def _load_from_file(self, path: Path) -> str | None:
        try:
            text = path.read_text(encoding="utf-8")
            if yaml is not None:
                data = yaml.safe_load(text)
                if isinstance(data, dict) and "content" in data:
                    return str(data["content"])
                _logger.warning("prompt file %s missing 'content' key", path)
                return None
            content = _load_content_from_yaml_subset(text)
            if content is not None:
                _logger.debug("loaded prompt %s with YAML subset fallback", path)
                return content
            _logger.debug(
                "PyYAML is not installed and prompt file %s is not subset-parseable",
                path,
            )
        except Exception as e:  # noqa: BLE001
            _logger.warning("failed to load prompt from %s: %s", path, e)
        return None

    def get(self, name: str, default: str | None = None) -> str:
        if name in self._cache:
            return self._cache[name]

        # Try YAML file first
        file_path = self._find_file(name)
        if file_path is not None:
            content = self._load_from_file(file_path)
            if content is not None:
                self._cache[name] = content
                self._file_map[name] = file_path
                return content

        # Fallback to builtin
        if name in _BUILTIN:
            return _BUILTIN[name]

        # Final fallback
        if default is not None:
            return default
        raise KeyError(f"Prompt '{name}' not found (search dirs: {self._search_dirs})")

    def reload(self, name: str) -> str:
        file_path = self._find_file(name)
        if file_path is not None:
            content = self._load_from_file(file_path)
            if content is not None:
                self._cache[name] = content
                self._file_map[name] = file_path
                _logger.info("reloaded prompt '%s' from %s", name, file_path)
                return content
            _logger.warning("reload '%s' failed · falling back to builtin", name)
        # Fallback to builtin
        if name in _BUILTIN:
            self._cache[name] = _BUILTIN[name]
            self._file_map.pop(name, None)
            return _BUILTIN[name]
        raise KeyError(f"Prompt '{name}' not found for reload")

    def list_available(self) -> list[str]:
        names = set(_BUILTIN.keys())
        for d in self._search_dirs:
            if d.exists():
                for p in d.glob("*.yaml"):
                    names.add(p.stem)
                for p in d.glob("*.yml"):
                    names.add(p.stem)
        return sorted(names)

    @property
    def loaded_from_files(self) -> dict[str, Path]:
        return dict(self._file_map)


_default_loader: PromptLoader | None = None


def _load_content_from_yaml_subset(text: str) -> str | None:
    """Parse the simple ``content: |`` YAML shape used by bundled prompts.

    This is intentionally tiny: it keeps lightweight imports stable when
    PyYAML is absent, while complex YAML still falls back to builtins.
    """

    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line[:1].isspace():
            continue
        stripped = line.strip()
        if not stripped.startswith("content:"):
            continue
        suffix = stripped[len("content:") :].strip()
        if suffix in {"|", "|-", "|+"}:
            return _read_yaml_block_scalar(lines[index + 1 :])
        if suffix in {'""', "''"}:
            return ""
        if suffix:
            return suffix.strip('"').strip("'")
        return ""
    return None


def _read_yaml_block_scalar(lines: list[str]) -> str:
    block: list[str] = []
    base_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if base_indent is not None:
                block.append("")
            continue
        indent = len(line) - len(line.lstrip(" "))
        if base_indent is None:
            base_indent = indent
        if indent < base_indent:
            break
        block.append(line[base_indent:])
    return "\n".join(block).rstrip("\n")


def get_prompt_loader() -> PromptLoader:
    global _default_loader
    if _default_loader is None:
        dirs_str = os.environ.get("ECHO_PROMPT_DIRS", "")
        dirs = [Path(d.strip()) for d in dirs_str.split(":") if d.strip()] if dirs_str else None
        _default_loader = PromptLoader(search_dirs=dirs)
    return _default_loader


def get_prompt(name: str, default: str | None = None) -> str:
    return get_prompt_loader().get(name, default)


def reload_prompt(name: str) -> str:
    return get_prompt_loader().reload(name)


# Hot-reload-capable registry (additive · see registry.py for details).
from runtime.platform.prompts.registry import PromptRegistry as PromptRegistry  # noqa: E402
from runtime.platform.prompts.seed import render as render  # noqa: E402
from runtime.platform.prompts.seed import seed_if_empty as seed_if_empty
