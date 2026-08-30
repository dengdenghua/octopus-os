"""Strip model-controllable privilege escalation before dispatch.

A handful of skill-handler parameters are *internal privilege overrides*,
not real tool inputs:

* ``allow_sensitive`` — tells :func:`path_guard.check_path` to skip the
  sensitive-file denylist (``~/.ssh``, ``/etc/shadow``, …) *and* the
  user-configured read denylist, even inside the sandbox.
* ``allow_private`` — tells :func:`url_guard.check_url` to skip the SSRF /
  private-IP protection.

``tool_spec_builder`` hides ``allow_sensitive`` from the published tool
schema, but the schema is ``additionalProperties: True`` (skills carry no
formal parameter schema, so the model infers arg names from the
description). That means a model — or, worse, an *indirect prompt
injection* riding in tool output — can still smuggle ``allow_sensitive`` /
``allow_private`` into a tool call's input dict, and the executor passes the
dict straight to ``handler(**args)``. The result is a read of in-workspace
secrets (``.env``) or an SSRF to the internal network, defeating guards
that are otherwise correct.

Delegation tools also accept nested ``context`` dictionaries. Security state
inside those dictionaries is monotonic: a child may inherit parent sandbox,
workspace, approval, network, routing, and injection-taint state, but a model
must not replace or clear it through ``context`` or ``specs[*].context``.

The model never has a legitimate reason to set these. Trusted internal /
admin / audit callers that genuinely need the override invoke the skill
handlers (or ``check_path`` / ``check_url``) **directly**, never through the
model tool-call path. So every model→handler boundary drops them first.
"""

from __future__ import annotations

import re
from typing import Any

# Internal privilege overrides that must never be honoured when they arrive
# in model-supplied tool input. Keep in sync with the rationale above and
# with ``tool_spec_builder._INTERNAL_PARAMS`` (which only governs schema
# *visibility*, not runtime enforcement).
MODEL_FORBIDDEN_ARGS: frozenset[str] = frozenset(
    {
        "allow_sensitive",
        "allow_private",
    }
)

MODEL_PROTECTED_CONTEXT_PREFIXES: tuple[str, ...] = (
    "approval",
    "sandbox",
    "permission",
    "network",
    "workspace",
    "runtimesession",
    "toolallowlist",
    "extratool",
    "directskill",
    "skillpack",
    "plugingrant",
    "enablesubagent",
    "reviewqueue",
    "subagentpolicy",
    "actor",
    "session",
    "threadid",
    "callerthread",
    "promptinjection",
    "inheritedinjection",
    "deniedpath",
    # Audit F-01: the react-drive dispatch path is a trusted-side decision
    # (bridge.call_subagent stamps react_stack / react_loop_subagent, and the
    # runner honors react_loop_max_iterations). A model must not steer a
    # spawn onto/off the MAIN react loop, inflate its iteration budget, or
    # forge a stack object.
    "reactloop",
    "reactstack",
)


def is_model_protected_context_key(key: str) -> bool:
    text = str(key or "").strip()
    if not text or text.startswith("_"):
        return True
    canonical = re.sub(r"[^a-z0-9]", "", text.lower())
    return any(canonical.startswith(prefix) for prefix in MODEL_PROTECTED_CONTEXT_PREFIXES)


def strip_model_controlled_overrides(
    args: Any,
) -> tuple[Any, list[str]]:
    """Return ``args`` with model-forbidden privilege escalation removed.

    Returns a ``(cleaned_args, stripped_keys)`` tuple. ``stripped_keys`` is
    a sorted list of the keys that were dropped (empty when nothing was
    stripped), suitable for audit/telemetry. ``args`` is returned unchanged
    (same object) when it is not a dict or carries none of the flags, so the
    common path allocates nothing.
    """
    if not isinstance(args, dict):
        return args, []
    stripped = sorted(k for k in MODEL_FORBIDDEN_ARGS if k in args)
    cleaned = {k: v for k, v in args.items() if k not in MODEL_FORBIDDEN_ARGS}
    cleaned, context_stripped, context_changed = _strip_delegation_context_overrides(cleaned)
    stripped.extend(context_stripped)
    if not stripped and not context_changed:
        return args, []
    return cleaned, sorted(set(stripped))


def _strip_delegation_context_overrides(
    value: Any,
    *,
    path: str = "",
) -> tuple[Any, list[str], bool]:
    if isinstance(value, list):
        changed = False
        stripped: list[str] = []
        rows: list[Any] = []
        for index, item in enumerate(value):
            child, child_stripped, child_changed = _strip_delegation_context_overrides(
                item,
                path=f"{path}[{index}]",
            )
            rows.append(child)
            stripped.extend(child_stripped)
            changed = changed or child_changed
        return (rows if changed else value), stripped, changed
    if not isinstance(value, dict):
        return value, [], False

    changed = False
    stripped = []
    output: dict[Any, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        child_path = f"{path}.{key_text}" if path else key_text
        if key_text.lower() == "context" and isinstance(item, dict):
            safe_context: dict[Any, Any] = {}
            for context_key, context_value in item.items():
                if is_model_protected_context_key(str(context_key)):
                    stripped.append(f"{child_path}.{context_key}")
                    changed = True
                    continue
                safe_context[context_key] = context_value
            item = safe_context
        child, child_stripped, child_changed = _strip_delegation_context_overrides(
            item,
            path=child_path,
        )
        output[key] = child
        stripped.extend(child_stripped)
        changed = changed or child_changed
    return (output if changed else value), stripped, changed
