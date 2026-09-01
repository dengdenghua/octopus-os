"""EvolutionRouter · route evolution candidates to the right forge.

Biomimetic alias: the *Eyes* (perception/routing layer) deciding which
*Regeneration* path a candidate takes.

Decision logic
--------------
A candidate goes to **ReflexForge** (CPU fast path) when:
  * The reply is pure text (no tool calls, no file ops, no code changes).
  * The prompt is short and stable (high consistency across samples).
  * The reply doesn't reference external resources (files, APIs, searches).

A candidate goes to **SkillForge** (model/skill path) when:
  * The trajectory involves tool calls or multi-step skill sequences.
  * The reply depends on dynamic context (file contents, search results).

Both forges run independently on the scheduler tick. The EvolutionRouter
is a *classification* layer that tags each candidate with its recommended
path, so operators can inspect the decision in the admin UI.

Usage::

    from runtime.safety.recovery.evolution_router import EvolutionRouter
    router = EvolutionRouter()
    path = router.classify_candidate(prompt, reply, has_tool_calls=False)
    # path == "reflex"  →  ReflexForge
    # path == "skill"   →  SkillForge
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

_LOG = logging.getLogger(__name__)

EvolutionPath = Literal["reflex", "skill", "unknown"]

# ═══════════════════════════════════════════════════════════
# Heuristics
# ═══════════════════════════════════════════════════════════

# Prompts containing these keywords suggest dynamic / tool-dependent
# work that a pure-text reflex rule can't handle.
_TOOL_DEPENDENT_KEYWORDS = (
    # File operations
    "read file",
    "write file",
    "edit file",
    "open file",
    "create file",
    "delete file",
    "读取文件",
    "写入文件",
    "编辑文件",
    "打开文件",
    "创建文件",
    "删除文件",
    # Code operations
    "run code",
    "execute",
    "compile",
    "build",
    "test",
    "debug",
    "refactor",
    "运行代码",
    "执行",
    "编译",
    "构建",
    "测试",
    "调试",
    "重构",
    # Search / research
    "search",
    "research",
    "lookup",
    "find online",
    "搜索",
    "调研",
    "查找",
    # System operations
    "install",
    "deploy",
    "shell",
    "terminal",
    "command",
    "安装",
    "部署",
)

# Reply markers that indicate the reply is dynamic (depends on context
# that won't be the same next time).
_DYNAMIC_REPLY_MARKERS = (
    "```",  # code blocks — usually file-specific
    "http://",
    "https://",
    "/Users/",
    "/home/",
    "C:\\\\",  # Windows paths
    "exit code",
    "stderr",
    "stdout",
)


@dataclass(frozen=True)
class EvolutionVerdict:
    """The router's decision for one candidate."""

    path: EvolutionPath
    reason: str
    confidence: float
    signals: dict[str, Any]


class EvolutionRouter:
    """Classify evolution candidates into reflex vs skill paths.

    The router is stateless and cheap — it's called per candidate
    during the propose phase. The actual forging is done by
    ``ReflexForge`` and ``SkillForge`` independently.
    """

    def classify_candidate(
        self,
        prompt: str,
        reply: str,
        *,
        has_tool_calls: bool = False,
        has_code_changes: bool = False,
        step_count: int = 0,
    ) -> EvolutionVerdict:
        """Decide whether a candidate should become a reflex rule or a skill.

        Parameters
        ----------
        prompt
            The user's input prompt.
        reply
            The assistant's reply text (empty if the turn was tool-only).
        has_tool_calls
            Whether the trajectory involved any tool/sucker calls.
        has_code_changes
            Whether the turn produced file modifications.
        step_count
            Number of steps in the trajectory (0 for pure-text turns).
        """
        signals: dict[str, Any] = {
            "has_tool_calls": has_tool_calls,
            "has_code_changes": has_code_changes,
            "step_count": step_count,
            "prompt_length": len(prompt),
            "reply_length": len(reply),
        }

        # Hard signals: if the turn involved tools or code changes,
        # it's a skill candidate — a reflex rule can't reproduce
        # tool calls.
        if has_tool_calls or has_code_changes or step_count >= 2:
            return EvolutionVerdict(
                path="skill",
                reason=(
                    f"trajectory involves tools/code/steps "
                    f"(tools={has_tool_calls}, code={has_code_changes}, "
                    f"steps={step_count})"
                ),
                confidence=0.9,
                signals=signals,
            )

        # Soft signals: check prompt for tool-dependent keywords.
        prompt_lower = prompt.lower()
        tool_keyword_hits = [kw for kw in _TOOL_DEPENDENT_KEYWORDS if kw in prompt_lower]
        if tool_keyword_hits:
            signals["tool_keyword_hits"] = tool_keyword_hits
            return EvolutionVerdict(
                path="skill",
                reason=f"prompt contains tool-dependent keywords: {tool_keyword_hits[:3]}",
                confidence=0.7,
                signals=signals,
            )

        # Check reply for dynamic content markers.
        dynamic_hits = [m for m in _DYNAMIC_REPLY_MARKERS if m in reply]
        if dynamic_hits:
            signals["dynamic_reply_markers"] = dynamic_hits
            return EvolutionVerdict(
                path="skill",
                reason=f"reply contains dynamic markers: {dynamic_hits[:3]}",
                confidence=0.65,
                signals=signals,
            )

        # Pure-text candidate → reflex path.
        # Short prompt + text-only reply + no tools = ideal reflex rule.
        confidence = 0.8
        if len(prompt) <= 50:
            confidence = 0.9
        if len(reply) <= 200:
            confidence = min(0.95, confidence + 0.05)

        return EvolutionVerdict(
            path="reflex",
            reason=(
                "pure-text prompt+reply with no tool calls, no code changes, no dynamic markers"
            ),
            confidence=confidence,
            signals=signals,
        )

    def classify_batch(
        self,
        candidates: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """Classify a batch of candidates and group them by path.

        Each candidate dict should have:
          - prompt: str
          - reply: str
          - has_tool_calls: bool (optional)
          - has_code_changes: bool (optional)
          - step_count: int (optional)

        Returns a dict with keys "reflex", "skill", "unknown",
        each containing the candidates assigned to that path
        (with the verdict appended).
        """
        grouped: dict[str, list[dict[str, Any]]] = {
            "reflex": [],
            "skill": [],
            "unknown": [],
        }
        for cand in candidates:
            prompt = str(cand.get("prompt") or "")
            reply = str(cand.get("reply") or "")
            verdict = self.classify_candidate(
                prompt,
                reply,
                has_tool_calls=bool(cand.get("has_tool_calls", False)),
                has_code_changes=bool(cand.get("has_code_changes", False)),
                step_count=int(cand.get("step_count", 0)),
            )
            enriched = dict(cand)
            enriched["evolution_verdict"] = {
                "path": verdict.path,
                "reason": verdict.reason,
                "confidence": verdict.confidence,
                "signals": verdict.signals,
            }
            grouped[verdict.path].append(enriched)
        return grouped


__all__ = [
    "EvolutionPath",
    "EvolutionRouter",
    "EvolutionVerdict",
]
