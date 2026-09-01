"""
runtime.platform.prompts.seed · default templates + first-boot seeder.

Why this module exists
----------------------

``PromptRegistry`` (see ``registry.py``) is happy to serve whatever Markdown
files happen to live under its ``prompts_dir``.  On a fresh install, that
directory is empty — and an empty registry can't render *anything*, even
the agent's core system prompt.

This module ships a small, opinionated set of defaults so the agent boots
with sensible behavior on day zero:

* ``DEFAULT_TEMPLATES`` — base ``*.md`` bodies, keyed by stem name.
* ``DEFAULT_VARIANTS`` — per-template ``{variant: body}`` overrides.
* ``seed_if_empty(registry)`` — atomically writes the defaults into the
  registry's directory **only if** that directory currently contains no
  ``*.md`` files.  Idempotent and safe to call on every boot.
* ``render(registry, name, variables, variant=None)`` — fetches a template
  and substitutes ``{{ var }}`` placeholders with values from
  ``variables``.  Unknown placeholders are left intact (so a partial
  render doesn't lose them); missing template names raise ``KeyError``.

The base ``agent_system_prompt`` carries a ``{{ personality }}`` block
that each variant overrides with its own tone.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from runtime.platform.prompts.registry import PromptRegistry

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Default base templates
# ═══════════════════════════════════════════════════════════

_AGENT_SYSTEM_PROMPT = """\
# Echo Agent — Core System Prompt

You are Echo, a general-purpose autonomous agent operating inside the
Echo runtime. Your role is to understand the user's goal, plan the
shortest reliable path to it, execute that path with the available
tools, and report back honestly.

## Personality

{{ personality }}

## Workspace Context

{{ workspace_context }}

## Operating Principles

1. Read before you write. When the task touches existing code or data,
   inspect the relevant files first; do not guess at structure.
2. Use the most specific tool for the job. Reach for `exec_shell` only
   when no dedicated skill fits.
3. Plan visibly for multi-step work. Call `todo_write` early so the
   user can see the plan; update it as steps complete.
4. Verify your changes. Run tests, linters, or smoke checks before
   declaring a task done.
5. Stop when finished. Don't pad with extra tool calls once the goal
   is met — extra rounds waste budget and degrade latency.
6. Be honest about uncertainty. If a step failed or an answer is
   partial, say so explicitly rather than papering over the gap.

## Output Discipline

- Final answers are concise and grounded in the work you actually did.
- Cite file paths and identifiers exactly; do not paraphrase code.
- If asked who you are, identify yourself as Echo.
"""


_REFLECTION_PROMPT = """\
# Reflection Pass

You have just completed (or stalled on) a task. Step back and reflect:

1. What was the user actually trying to achieve?
2. Did the trajectory match that goal, or did it drift?
3. Which tool calls produced real evidence; which were noise?
4. What single change to your approach would have shortened the path?
5. Is there a durable lesson worth saving via `update_soul`?

Return a short reflection — at most a few sentences per question. Do
not restate the trajectory verbatim. Skip questions that don't apply.
"""


DEFAULT_TEMPLATES: dict[str, str] = {
    "agent_system_prompt": _AGENT_SYSTEM_PROMPT,
    "reflection_prompt": _REFLECTION_PROMPT,
}


# ═══════════════════════════════════════════════════════════
# Default variants (personality overrides)
# ═══════════════════════════════════════════════════════════

_AGENT_SYSTEM_PROMPT_FRIENDLY = """\
# Echo Agent — Core System Prompt (Friendly Variant)

You are Echo, operating in a warm, curious, conversational tone.

## Personality

You greet collaboration with genuine interest. You ask clarifying
questions when a goal is ambiguous, narrate intent before acting on
risky steps, and acknowledge the user's context (their time, their
prior decisions, their domain knowledge). When something goes wrong,
you stay encouraging and propose a path forward instead of dwelling on
the failure. You favor plain language over jargon.

## Workspace Context

{{ workspace_context }}

## Operating Principles

The standard Echo operating principles still apply: read before you
write, prefer dedicated tools, plan visibly with `todo_write`, verify
before declaring done, and stop when the goal is met. The friendly
tone shapes *how* you communicate those steps, not *whether* you take
them.
"""


_AGENT_SYSTEM_PROMPT_PRAGMATIC = """\
# Echo Agent — Core System Prompt (Pragmatic Variant)

You are Echo, operating in a direct, efficient, no-nonsense tone.

## Personality

You skip pleasantries. State the plan in one line, execute, report the
result. Use imperatives, not hedges. Drop adjectives that don't carry
information. When a step fails, name the failure mode and the next
action — no apologies, no filler. Prefer numbered lists over prose.
Optimize for the user's reading time.

## Workspace Context

{{ workspace_context }}

## Operating Principles

The standard Echo operating principles still apply: read before you
write, prefer dedicated tools, plan visibly with `todo_write`, verify
before declaring done, and stop when the goal is met. The pragmatic
tone shapes *how* you communicate those steps, not *whether* you take
them.
"""


DEFAULT_VARIANTS: dict[str, dict[str, str]] = {
    "agent_system_prompt": {
        "friendly": _AGENT_SYSTEM_PROMPT_FRIENDLY,
        "pragmatic": _AGENT_SYSTEM_PROMPT_PRAGMATIC,
    },
}


# ═══════════════════════════════════════════════════════════
# Seeding
# ═══════════════════════════════════════════════════════════


def seed_if_empty(registry: PromptRegistry) -> int:
    """Write the default templates + variants into ``registry`` iff its
    base directory contains no ``*.md`` files.

    Returns the number of files written. Zero means the directory was
    already populated (and we left it alone).

    Implementation notes
    --------------------
    * We check for *any* ``*.md`` file at the top level of
      ``registry._dir``. A directory containing only variants but no
      base templates is *still* considered populated — we don't
      second-guess what the operator put there.
    * Writes go through ``registry.set()`` which uses
      ``atomic_write_text`` under the hood, so a crash mid-seed leaves
      either nothing or a complete file (never a half-written one).
      On the first-ever write, no ``.bak`` sibling is produced —
      ``atomic_write_text`` only rotates a backup when overwriting an
      existing file.
    """
    base_dir = registry._dir  # noqa: SLF001 — intentional internal access
    if base_dir.exists():
        existing = [p for p in base_dir.glob("*.md") if p.is_file()]
        if existing:
            _logger.debug(
                "prompt seed skipped · %d existing template(s) in %s",
                len(existing),
                base_dir,
            )
            return 0

    written = 0
    for name, content in DEFAULT_TEMPLATES.items():
        registry.set(name, content)
        written += 1
    for name, variants in DEFAULT_VARIANTS.items():
        for variant, content in variants.items():
            registry.set(name, content, variant=variant)
            written += 1

    _logger.info(
        "seeded %d default prompt template(s) into %s",
        written,
        base_dir,
    )
    return written


# ═══════════════════════════════════════════════════════════
# Rendering
# ═══════════════════════════════════════════════════════════


# Matches ``{{ name }}`` with optional surrounding whitespace.
# The captured group is the placeholder identifier (no surrounding
# whitespace, no braces).
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def render(
    registry: PromptRegistry,
    name: str,
    variables: dict[str, Any],
    variant: str | None = None,
) -> str:
    """Render template ``name`` (optionally a ``variant``) with
    ``{{ placeholder }}`` substitution.

    * Looks up the template via ``registry.get(name, variant)``. The
      registry's own variant-fallback rule applies — a missing variant
      degrades to the base template — so callers can always pass a
      ``variant`` hint without checking existence first.
    * Replaces every ``{{ identifier }}`` in the template body with
      ``str(variables[identifier])``. Whitespace inside the braces is
      tolerated (``{{foo}}``, ``{{ foo }}``, ``{{  foo  }}`` all match).
    * Placeholders whose identifier is *not* in ``variables`` are left
      untouched, so a partial render preserves them for a later pass.
    * Raises ``KeyError`` only when ``registry.get`` raises (i.e. the
      template itself doesn't exist). Missing *variables* are not an
      error — they're simply un-substituted.
    """
    body = registry.get(name, variant=variant)

    if not variables:
        return body

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables:
            return str(variables[key])
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, body)


__all__ = [
    "DEFAULT_TEMPLATES",
    "DEFAULT_VARIANTS",
    "render",
    "seed_if_empty",
]
