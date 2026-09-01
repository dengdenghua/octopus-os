"""
skill_library_skills · expose Kimi-style "learned skills" as 3 skills.

Sister to ``memory_skills`` but the scope is "templates derived from
documents" rather than "facts/lessons".

  - ``learn_skill_from_text`` · feed a sample doc, agent extracts
    structural+stylistic DNA, persists template at
    ``agents/<id>/skills/<name>.md``.
  - ``list_learned_skills`` · enumerate the agent's library.
  - ``apply_skill`` · use a saved template to produce output for a
    new but same-shape request.

All three resolve the active agent via ``current_session()`` —
without a Session they raise RuntimeError (same gate the other
per-agent skills use).
"""

from __future__ import annotations

from typing import Any

from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase


def _agent_id_from_session() -> str:
    """Reuse memory_skills' ``_agent_core_dir`` resolution (raises
    RuntimeError outside a Session) so all per-agent skills agree
    on agent_id resolution and fail-closed identically."""
    from .memory_skills import _agent_core_dir

    return _agent_core_dir().parent.name


def _learn_skill_from_text(
    name: str = "",
    sample_text: str = "",
    sample_source: str = "",
    golden_samples: list | None = None,
    golden_pass_threshold: float = 0.66,
    model: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    """Learn a reusable skill template from a sample doc.

    If ``golden_samples`` is a list of 3+ alternative user_requests,
    the extracted template is gated: it's only persisted if at least
    ``golden_pass_threshold`` (default 2/3) of the samples produce
    output that preserves ≥50% of the template's H2 headers. Failed
    extractions are dropped.
    """
    agent_id = _agent_id_from_session()
    from runtime.memory.skills_lib.skill_library import learn_skill_from_text

    # Coerce golden_samples · LLMs sometimes pass strings or None
    if isinstance(golden_samples, str):
        # Allow "a|b|c" string form for compat with simple tool schemas
        golden_samples = [s.strip() for s in golden_samples.split("|") if s.strip()]
    if golden_samples is not None and not isinstance(golden_samples, list):
        golden_samples = None
    return learn_skill_from_text(
        agent_id=agent_id,
        name=name,
        sample_text=sample_text,
        sample_source=sample_source,
        golden_samples=golden_samples,
        golden_pass_threshold=float(golden_pass_threshold) if golden_pass_threshold else 0.66,
        model=(model or None),
    )


def _list_learned_skills(**_kw: Any) -> dict[str, Any]:
    """List the agent's learned skill library."""
    agent_id = _agent_id_from_session()
    from runtime.memory.skills_lib.skill_library import list_learned_skills

    skills = list_learned_skills(agent_id)
    return {"ok": True, "count": len(skills), "skills": skills}


def _apply_skill(
    name: str = "",
    user_request: str = "",
    model: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    """Apply a learned skill to produce content for ``user_request``."""
    agent_id = _agent_id_from_session()
    from runtime.memory.skills_lib.skill_library import apply_skill

    return apply_skill(
        agent_id=agent_id,
        name=name,
        user_request=user_request,
        model=(model or None),
    )


def register_skill_library_skills(registry: SkillRegistry) -> int:
    registry.register(
        Skill(
            name="learn_skill_from_text",
            description=(
                "Learn a reusable output template from a high-quality "
                "sample. The agent feeds in text (e.g. a tech-comparison "
                "report copy-pasted from a PDF, or a slide outline), the "
                "LLM extracts the structural + stylistic DNA, and the "
                "result is persisted at "
                "agents/<your_id>/skills/<name>.md so YOU (or a future "
                "session) can `apply_skill(name, ...)` to produce "
                "same-shape outputs for a different topic.\n"
                "Args: {name: short slug like 'tech-comparison-report', "
                "sample_text: the doc text (≤ 8K chars), sample_source?: "
                "where it came from, golden_samples?: list of 3+ "
                "alternate user_requests (or pipe-separated string 'a|b|c') "
                "— if given, template is TESTED against them before "
                "persisting and rejected if < 2/3 preserve the template's "
                "H2 structure; golden_pass_threshold?: float (default 0.66), "
                "model?: override LLM}.\n"
                "EXPENSIVE · single LLM call (~3-5¢ haiku) per learn, "
                "+N more calls if golden_samples is provided."
            ),
            affinity=["skill_library", "self_evolution", "template"],
            cost_profile="mid",
            trusted_source="skill://private/learn_skill_from_text",
            handler=_learn_skill_from_text,
            tests=[
                SkillTestCase(
                    name="no_session_raises",
                    tier="golden",
                    args={"name": "x", "sample_text": "y"},
                    expect=SkillExpect(raises="RuntimeError"),
                ),
            ],
        )
    )

    registry.register(
        Skill(
            name="list_learned_skills",
            description=(
                "List skills you've previously learned via "
                "`learn_skill_from_text`. Returns "
                "{ok, count, skills:[{name, description, when_to_use, "
                "sample_source, learned_at, filename, size_bytes}]}. "
                "Use this to discover what shapes you can already "
                "reproduce before deciding to learn a new one."
            ),
            affinity=["skill_library"],
            cost_profile="low",
            trusted_source="skill://private/list_learned_skills",
            handler=_list_learned_skills,
            tests=[
                SkillTestCase(
                    name="no_session_raises",
                    tier="golden",
                    args={},
                    expect=SkillExpect(raises="RuntimeError"),
                ),
            ],
        )
    )

    registry.register(
        Skill(
            name="apply_skill",
            description=(
                "Apply a learned skill template to produce output "
                "matching its shape, for a new user request. The LLM "
                "is given the template + style notes and the user's "
                "specific request; reply is markdown matching the "
                "template's structure.\n"
                "Args: {name: skill name (slug), user_request: the "
                "specific topic / data to fill in, model?: override}.\n"
                "Use `list_learned_skills` first to see what's available."
            ),
            affinity=["skill_library", "template", "apply"],
            cost_profile="mid",
            trusted_source="skill://private/apply_skill",
            handler=_apply_skill,
            tests=[
                SkillTestCase(
                    name="no_session_raises",
                    tier="golden",
                    args={"name": "x", "user_request": "y"},
                    expect=SkillExpect(raises="RuntimeError"),
                ),
            ],
        )
    )
    return 3


__all__ = ["register_skill_library_skills"]
