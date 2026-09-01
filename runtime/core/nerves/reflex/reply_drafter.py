from __future__ import annotations

import json
import logging
import re
from typing import Any

_LOG = logging.getLogger("echo.reflex.draft")


_DRAFT_SYSTEM_PROMPT = """\
You are drafting canned reply text for a home-automation Agent OS
reflex layer. Each input is a user prompt that fires often enough
to deserve a fixed-string reply (faster than calling an LLM each
time). Your job: write a SHORT, helpful, action-oriented reply
for each prompt.

Constraints:
  * Reply in the SAME LANGUAGE as the prompt.
  * Keep it under 30 characters when possible · this is for
    reflex (instant) responses, not chat.
  * No emoji unless the prompt explicitly asks for one.
  * If the prompt is a command ("打开厨房灯"), confirm the action
    succinctly ("好的，灯已开"). Don't restate the command.
  * If the prompt is a question ("现在几点"), answer it as if the
    runtime can fetch the value · don't write code.
  * If the prompt is a greeting / thanks, respond conversationally.
  * NEVER write multi-paragraph essays · 1 line, period.

Return STRICT JSON of shape:
  {"replies": {"<prompt verbatim>": "<your reply>", ...}}

No commentary, no markdown fences, no other keys."""


def _build_prompt(suggestions: list[dict[str, Any]]) -> str:
    """Build the user message · just a JSON list of prompts the LLM
    needs to draft for. Keeping the input shape symmetric with the
    output shape makes the LLM's job near-mechanical."""
    prompts = [s.get("prompt", "") for s in suggestions if s.get("prompt")]
    payload = {"prompts": prompts}
    return "Draft a reflex reply for each of these prompts:\n\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


def _parse_replies(text: str) -> dict[str, str]:
    """Pull the ``{"replies": {...}}`` JSON out of the LLM response.

    Tolerates LLMs that wrap output in markdown fences or add a
    trailing comment · we strip everything before the first ``{``
    and after the matching ``}`` before parsing.
    """
    if not text:
        return {}
    # Strip markdown fences first.
    text = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.M).strip("` \n")
    # Find the first ``{`` and the last matching ``}``.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return {}
    candidate = text[start : end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        _LOG.warning("reply drafter: JSON parse failed · %s", exc)
        return {}
    replies = data.get("replies") if isinstance(data, dict) else None
    if not isinstance(replies, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in replies.items():
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out


def draft_replies(
    suggestions: list[dict[str, Any]],
    *,
    router: Any,
    model: str | None = None,
    max_drafts: int = 10,
) -> dict[str, str]:
    """Ask the LLM to draft replies for the given suggestions ·
    returns ``{prompt: drafted_reply}``. Bounded by ``max_drafts``
    so a tracker with hundreds of suggestions doesn't fan out into
    one giant prompt.

    ``router`` should be ``stack.planner.router`` (a ModelRouter).
    ``model`` defaults to the planner's default · pass an explicit
    id to draft on a cheaper / faster model (e.g. claude-haiku for
    bulk-draft jobs).
    """
    if not suggestions or router is None:
        return {}
    batch = suggestions[: max(1, int(max_drafts))]
    user_prompt = _build_prompt(batch)

    # Lazy import · this module is also imported by the test path
    # which doesn't want to pull in the eyes layer.
    from runtime.platform.models.llm import Message, ModelRequest

    messages = [
        Message(role="system", content=_DRAFT_SYSTEM_PROMPT),
        Message(role="user", content=user_prompt),
    ]
    effective_model = model or "claude-sonnet-4-6"
    req = ModelRequest(
        model=effective_model,
        messages=messages,
        max_tokens=1024,
        # Low temperature · we want consistent short replies, not
        # creative riffs. The system prompt is strict already.
        temperature=0.2,
    )
    try:
        resp = router.call(req)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("reply drafter: LLM call failed · %s", exc)
        return {}
    return _parse_replies(getattr(resp, "text", "") or "")


def apply_drafts_to_yaml(
    suggested_yaml: str,
    drafted_reply: str | None,
) -> str:
    """Replace the ``TODO`` placeholder in a suggested-yaml block
    with the drafted reply. Idempotent · a yaml without a TODO
    marker is returned unchanged. Does NOT touch any other line so
    the cluster comment / pattern / priority stay verbatim.
    """
    if not drafted_reply:
        return suggested_yaml
    # Escape double-quotes for embedding in the yaml string literal.
    escaped = drafted_reply.replace("\\", "\\\\").replace('"', '\\"')
    return re.sub(
        r'reply:\s*"TODO[^"]*"',
        f'reply: "{escaped}"',
        suggested_yaml,
        count=1,
    )


__all__ = ["draft_replies", "apply_drafts_to_yaml"]
