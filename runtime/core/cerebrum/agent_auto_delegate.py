"""Auto-delegate to pinned agents on the first ReAct step.

When a user prompt contains exactly one ``@agent:<id>`` mention, and
the prompt looks like a delegation request rather than a side-comment,
the runtime can short-circuit the model's first reasoning step by
calling that agent directly. This trades a small loss of model
flexibility for a meaningful latency drop (one fewer LLM round trip).

Heuristics for "looks like a delegation request":

1. Exactly one agent mention. Multiple mentions make it ambiguous —
   the model should orchestrate them.
2. The agent is in the runtime's registry (i.e. it actually exists).
3. The prompt's other content (with the @agent: token stripped) is
   non-trivial — at least 8 characters of substantive instruction.
4. The prompt does NOT contain a competing routing signal:
   - other @plugin:/@skill:/@pack: mentions
   - explicit "ask N people" / "compare" / "all agents" wording

The delegation is always a hint to the runtime, not a hard override:
callers are free to ignore the recommendation when other constraints
(safety, mode, tool budget) demand model orchestration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from runtime.core.cerebrum.input_mentions import (
    InputMentions,
    parse_input_mentions,
)

# Wording that signals "fan out to multiple agents" — when the user
# explicitly wants comparison or polling, we do NOT auto-delegate to
# the single mentioned agent because the model should orchestrate.
_FAN_OUT_PATTERNS = (
    re.compile(r"\bask\s+(?:everyone|all|both|each|several|multiple)\b", re.I),
    re.compile(r"\bcompare\b.*\bagents?\b", re.I),
    re.compile(r"\bvote|consensus|poll\b", re.I),
    re.compile(r"对比|对照|多人|大家|所有(?:人|agent)", re.I),
)


@dataclass(frozen=True)
class AgentDelegationPlan:
    """Recommended single-agent delegation, or empty when none fits."""

    target_agent: str | None
    cleaned_prompt: str  # original prompt with the @agent: token stripped
    reason: str  # human-readable reason; empty when not delegating

    @property
    def should_delegate(self) -> bool:
        return bool(self.target_agent)


def plan_auto_delegation(
    prompt: str,
    *,
    registry: object | None = None,
) -> AgentDelegationPlan:
    """Decide whether this prompt should auto-delegate to one agent.

    Parameters
    ----------
    prompt :
        The user's full prompt text (including any @-mentions).
    registry :
        An object exposing ``has(name)`` for agent existence. When
        ``None`` the existence check is skipped.

    Returns
    -------
    AgentDelegationPlan
        ``should_delegate=True`` only when all heuristics pass.
    """
    text = prompt or ""
    if not text.strip():
        return AgentDelegationPlan(None, text, "")

    mentions: InputMentions = parse_input_mentions(text)

    # Heuristic 1: exactly one agent mention.
    if len(mentions.agents) != 1:
        return AgentDelegationPlan(
            None,
            text,
            "no single agent mention"
            if not mentions.agents
            else f"{len(mentions.agents)} agent mentions — needs orchestration",
        )
    target = mentions.agents[0]

    # Heuristic: no competing routing signals from other mention types.
    # (Skill/plugin/pack mentions imply the user wants the *current*
    # context to use those — not to forward the whole prompt to an
    # agent that may or may not have access to them.)
    if mentions.skills or mentions.plugins or mentions.packs:
        return AgentDelegationPlan(
            None,
            text,
            "competing skill/plugin/pack pin — let model orchestrate",
        )

    # Heuristic 4: fan-out wording.
    for pattern in _FAN_OUT_PATTERNS:
        if pattern.search(text):
            return AgentDelegationPlan(
                None,
                text,
                "fan-out wording detected",
            )

    # Heuristic 2: agent exists in the registry.
    if registry is not None and hasattr(registry, "has"):
        try:
            if not registry.has(target):
                return AgentDelegationPlan(
                    None,
                    text,
                    f"agent `{target}` not in registry",
                )
        except (AttributeError, TypeError, ValueError):
            # If the registry call blows up, default to NOT delegating
            # — better to let the model reason than to forward to a
            # half-resolved target.
            return AgentDelegationPlan(
                None,
                text,
                "registry lookup failed",
            )

    # Heuristic 3: substantive instruction beyond the mention itself.
    cleaned = _strip_agent_token(text, target)
    substantive = cleaned.strip()
    if len(substantive) < 8:
        return AgentDelegationPlan(
            None,
            text,
            "prompt is just the mention — model should ask for context",
        )

    return AgentDelegationPlan(
        target_agent=target,
        cleaned_prompt=cleaned,
        reason=f"single @agent:{target} pin with substantive instruction",
    )


def _strip_agent_token(text: str, agent_id: str) -> str:
    """Remove ``@agent:<id>`` from the text once."""
    token_re = re.compile(
        r"@agent:" + re.escape(agent_id) + r"(?=\b|[\s,.;:!?])",
    )
    return token_re.sub("", text, count=1).replace("  ", " ").strip()


__all__ = [
    "AgentDelegationPlan",
    "plan_auto_delegation",
]
