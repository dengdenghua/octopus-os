"""
skill_library · Kimi-style "skills from documents" · per-agent Markdown
templates the agent can learn from a sample and reuse later.

What this is
------------

Two complementary capabilities:

1. ``learn_skill_from_text`` · the agent sees a high-quality
   sample (e.g. a technical comparison report copy-pasted from a
   PDF) and asks the LLM to extract its structural + stylistic
   DNA — section headers, tables, scoring conventions, length,
   tone — into a reusable template. Lands at
   ``agents/<agent_id>/skills/<name>.md``.

2. ``apply_skill`` · later, when the user asks for "the same kind
   of report on X", the agent looks up the saved skill and feeds
   the template back to the LLM along with the user's request.
   The output should match the original sample's shape.

Together they let the agent inherit a quality bar from one good
example, instead of re-discovering the layout every time.

NOT a Claude `Skill` (the SOP / runtime-injected guide kind from
Anthropic). That's a separate concept; this is the document-driven
template variant Kimi K2.6 ships.

File format
-----------

Each saved skill is a markdown file with YAML frontmatter:

    ---
    name: tech-comparison-report
    description: short tag for catalog / search
    when_to_use: triggers the LLM uses to pick this skill
    sample_source: where the original sample came from (free text)
    learned_at: ISO timestamp
    ---

    # Output Template

    <markdown skeleton with placeholders the agent fills in>

    # Style Notes

    <bullet list of stylistic conventions to preserve>

Files live at ``agents/<agent_id>/skills/`` so each persona has
its own library. List via ``list_learned_skills``; load+apply via
``apply_skill``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.platform.llm_infra.llm_caller import LLMCaller
from runtime.platform.process.service_provider import get_provider

_LOG = logging.getLogger("echo.skill_library")

_SKILL_CALLER = LLMCaller("skill_router", "skill_default_model")


def _llm_call(
    *,
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int = 2500,
    temperature: float = 0.2,
) -> tuple[str, dict[str, Any]]:
    """Module-scope delegate for the skill router call.

    Kept as a named function (not a method on ``_SKILL_CALLER``) so
    tests can monkey-patch ``skill_library._llm_call`` to stub the
    LLM round-trip without touching the caller instance.
    """
    return _SKILL_CALLER.call(
        system=system,
        user=user,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def set_skill_router(router: Any, *, default_model: str | None = None) -> None:
    """Bootstrap hook · install the LLM router used for skill
    extraction + application. Pass ``None`` to disable."""
    provider = get_provider()
    provider.register_instance("skill_router", router)
    provider.register_instance("skill_default_model", default_model)


# ── Storage paths ────────────────────────────────────────


def _project_root() -> Path:
    from runtime.platform.process.paths import project_root

    return project_root()


def _skills_dir(agent_id: str) -> Path:
    return _project_root() / "agents" / agent_id / "skills"


_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _normalize_name(name: str) -> str:
    """Slugify into a safe filename · lowercase, dash-separated."""
    s = re.sub(r"[^a-z0-9_-]+", "-", str(name or "").strip().lower())
    s = s.strip("-_")
    return s[:64] or "unnamed-skill"


# ═══════════════════════════════════════════════════════════
# learn_skill_from_text
# ═══════════════════════════════════════════════════════════

_LEARN_SYSTEM = """You are a skill-template extractor. Given a
high-quality sample document, you analyse its structural + stylistic
DNA and produce a reusable Markdown template + style notes that the
agent can use to produce same-shape outputs in the future.

Output ONLY a JSON envelope, no prose outside the fenced block.

Schema:
```json
{
  "description": "<one-sentence tag for catalog/search>",
  "when_to_use": "<concrete triggers · what user requests should call this>",
  "template": "<markdown skeleton · use placeholders like "
"{topic}, {options}, {decision_criterion}>",
  "style_notes": [
    "<bullet · concrete convention to preserve>",
    "<another bullet>"
  ]
}
```

Rules:
  - The template must be MARKDOWN with fenced code/tables intact ·
    keep the sample's structure (sections, tables, lists) but
    replace specific content with placeholders.
  - Style notes are SHORT bullets: scoring scale, table column
    count, decision-rationale length, citation format, etc.
  - Don't repeat the sample text verbatim · the template is a
    SHAPE not a copy.
"""

_LEARN_FENCED_JSON = re.compile(
    r"```(?:json)?\s*\n(\{.*?\})\s*\n```",
    re.DOTALL | re.IGNORECASE,
)


def _parse_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    m = _LEARN_FENCED_JSON.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, TypeError, ValueError):  # noqa: BLE001 — skill metadata corrupt; skip
            pass
    # bare json fallback
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def learn_skill_from_text(
    *,
    agent_id: str,
    name: str,
    sample_text: str,
    sample_source: str = "",
    golden_samples: list[str] | None = None,
    golden_pass_threshold: float = 0.66,
    model: str | None = None,
) -> dict[str, Any]:
    if not agent_id:
        return {"ok": False, "error": "agent_id required"}
    if not sample_text or not sample_text.strip():
        return {"ok": False, "error": "sample_text required"}
    safe_name = _normalize_name(name)
    if not _SKILL_NAME_RE.match(safe_name):
        return {
            "ok": False,
            "error": f"normalized name {safe_name!r} invalid",
        }

    user_msg = (
        f"AGENT: {agent_id}\n"
        f"PROPOSED SKILL NAME: {safe_name}\n"
        f"SAMPLE SOURCE: {sample_source or '(unknown)'}\n\n"
        f"### Sample (extract its DNA into a template)\n"
        f"```markdown\n{sample_text[:8000]}\n```\n\n"
        f"Now produce your JSON envelope."
    )
    text, meta = _llm_call(
        system=_LEARN_SYSTEM,
        user=user_msg,
        model=model,
        max_tokens=2500,
        temperature=0.2,
    )
    if not text:
        return {"ok": False, "error": meta.get("error") or "empty LLM response"}
    parsed = _parse_json(text)
    if parsed is None:
        return {
            "ok": False,
            "error": "could not parse JSON envelope",
            "raw_text": text[:500],
        }

    description = str(parsed.get("description") or "").strip()
    when_to_use = str(parsed.get("when_to_use") or "").strip()
    template = str(parsed.get("template") or "").strip()
    style_notes = parsed.get("style_notes") or []
    if not isinstance(style_notes, list):
        style_notes = []
    if not template:
        return {
            "ok": False,
            "error": "LLM didn't produce a template",
            "raw_text": text[:500],
        }

    # Compose the Markdown skill file.
    style_md = "\n".join(f"- {str(b).strip()}" for b in style_notes if b)
    md = (
        "---\n"
        f"name: {safe_name}\n"
        f"description: {description}\n"
        f"when_to_use: {when_to_use}\n"
        f"sample_source: {sample_source or '(unknown)'}\n"
        f"learned_at: {datetime.now().isoformat(timespec='seconds')}\n"
        f"learned_by_model: {meta.get('model', '')}\n"
        "---\n\n"
        "# Output Template\n\n"
        f"{template}\n\n"
        "# Style Notes\n\n"
        f"{style_md or '(none)'}\n"
    )

    # ── Golden-test gate (C1) ─────────────────────────────
    # Run ``apply_skill`` against the golden samples with the JUST-
    # extracted template (in-memory, not persisted yet) and check
    # the outputs preserve the template's structural spine.
    golden_report: dict[str, Any] | None = None
    if golden_samples:
        import re as _re

        template_h2s = {h.strip().lower() for h in _re.findall(r"^##\s+([^\n]+)", template, _re.M)}
        # Need ≥2 H2 headers in template to run the structural check
        # (otherwise the heuristic has nothing to compare against).
        if len(template_h2s) < 2:
            golden_report = {
                "ok": False,
                "reason": "template has < 2 H2 headers to check structure against",
                "template_h2_count": len(template_h2s),
            }
        else:
            results: list[dict[str, Any]] = []
            for i, sample in enumerate(golden_samples[:5], start=1):
                # Run apply with an in-memory template (bypass the
                # file-read path by constructing the same markdown).
                apply_user = (
                    f"### SKILL: {safe_name}\n"
                    f"Description: {description}\n\n"
                    f"### TEMPLATE + STYLE NOTES\n```markdown\n{md}\n```\n\n"
                    f"### USER REQUEST\n{sample}\n\n"
                    f"Now produce the markdown content."
                )
                out_text, out_meta = _llm_call(
                    system=_APPLY_SYSTEM,
                    user=apply_user,
                    model=model,
                    max_tokens=2500,
                    temperature=0.3,
                )
                if not out_text:
                    results.append(
                        {
                            "sample_idx": i,
                            "passed": False,
                            "reason": "empty LLM response",
                            "h2_overlap": 0.0,
                        }
                    )
                    continue
                out_h2s = {
                    h.strip().lower() for h in _re.findall(r"^##\s+([^\n]+)", out_text, _re.M)
                }
                # Compute overlap: what fraction of template H2s
                overlap = len(template_h2s & out_h2s) / len(template_h2s) if template_h2s else 0.0
                passed = overlap >= 0.5 and len(out_text) >= 50
                results.append(
                    {
                        "sample_idx": i,
                        "passed": passed,
                        "h2_overlap": round(overlap, 2),
                        "output_chars": len(out_text),
                    }
                )
            pass_count = sum(1 for r in results if r["passed"])
            pass_rate = pass_count / max(1, len(results))
            golden_report = {
                "ok": pass_rate >= golden_pass_threshold,
                "pass_count": pass_count,
                "total": len(results),
                "pass_rate": round(pass_rate, 2),
                "threshold": golden_pass_threshold,
                "results": results,
                "template_h2_count": len(template_h2s),
            }
            if not golden_report["ok"]:
                return {
                    "ok": False,
                    "error": (
                        f"golden-test gate failed: {pass_count}/{len(results)} "
                        f"samples passed · threshold {golden_pass_threshold} · "
                        f"template NOT persisted"
                    ),
                    "golden_report": golden_report,
                }

    # Persist · always create the dir, allow overwrite (re-learning
    # an existing skill from a better sample is the intended flow).
    skills_dir = _skills_dir(agent_id)
    try:
        skills_dir.mkdir(parents=True, exist_ok=True)
        path = skills_dir / f"{safe_name}.md"
        path.write_text(md, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"persist failed: {type(exc).__name__}: {exc}",
        }

    result = {
        "ok": True,
        "name": safe_name,
        "path": str(path),
        "description": description,
        "when_to_use": when_to_use,
        "style_notes_count": len(style_notes),
        "template_chars": len(template),
        "meta": meta,
    }
    if golden_report:
        result["golden_report"] = golden_report
    return result


# ═══════════════════════════════════════════════════════════
# list / apply
# ═══════════════════════════════════════════════════════════


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Tiny YAML-ish frontmatter parser · key: value lines only."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for i, ln in enumerate(lines[1:], start=1):
        if ln.strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta: dict[str, str] = {}
    for raw in lines[1:end]:
        if ":" not in raw:
            continue
        k, v = raw.split(":", 1)
        meta[k.strip()] = v.strip()
    body = "\n".join(lines[end + 1 :]).lstrip()
    return meta, body


def list_learned_skills(agent_id: str) -> list[dict[str, Any]]:
    """Enumerate all skills under ``agents/<agent_id>/skills/``."""
    if not agent_id:
        return []
    sdir = _skills_dir(agent_id)
    if not sdir.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(sdir.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(text)
            out.append(
                {
                    "name": meta.get("name") or p.stem,
                    "description": meta.get("description", ""),
                    "when_to_use": meta.get("when_to_use", ""),
                    "sample_source": meta.get("sample_source", ""),
                    "learned_at": meta.get("learned_at", ""),
                    "filename": p.name,
                    "size_bytes": p.stat().st_size,
                }
            )
        except (OSError, TypeError, ValueError):
            continue
    return out


_APPLY_SYSTEM = """You are filling in a learned skill template. The
user will provide a SKILL TEMPLATE (with style notes) and a USER
REQUEST. Produce the requested content following the template's
shape AND the style notes EXACTLY. Do not deviate from the structure.

Reply with ONLY the filled-in markdown · no preamble, no postamble,
no JSON envelope. Just the content."""


def apply_skill(
    *,
    agent_id: str,
    name: str,
    user_request: str,
    model: str | None = None,
) -> dict[str, Any]:
    """Look up a learned skill and produce content following its
    template + style notes for ``user_request``.

    Returns ``{ok, output, name, meta}`` · ``output`` is the
    generated markdown.
    """
    if not agent_id:
        return {"ok": False, "error": "agent_id required"}
    if not user_request or not user_request.strip():
        return {"ok": False, "error": "user_request required"}
    safe_name = _normalize_name(name)
    path = _skills_dir(agent_id) / f"{safe_name}.md"
    if not path.exists():
        return {
            "ok": False,
            "error": f"skill {safe_name!r} not found in agent's library",
        }
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)

    user_msg = (
        f"### SKILL: {safe_name}\n"
        f"Description: {meta.get('description', '')}\n\n"
        f"### TEMPLATE + STYLE NOTES\n```markdown\n{body}\n```\n\n"
        f"### USER REQUEST\n{user_request}\n\n"
        f"Now produce the markdown content."
    )
    output, run_meta = _llm_call(
        system=_APPLY_SYSTEM,
        user=user_msg,
        model=model,
        max_tokens=3500,
        temperature=0.3,
    )
    if not output:
        return {
            "ok": False,
            "error": run_meta.get("error") or "empty LLM response",
            "meta": run_meta,
        }

    # Record usage for the Curator lifecycle tracker.
    try:
        from runtime.memory.skills_lib.skill_curator import record_use

        record_use(agent_id, safe_name)
    except ImportError:  # noqa: BLE001 — embedding model optional
        pass

    return {
        "ok": True,
        "name": safe_name,
        "output": output,
        "meta": run_meta,
    }


__all__ = [
    "set_skill_router",
    "learn_skill_from_text",
    "list_learned_skills",
    "apply_skill",
]
