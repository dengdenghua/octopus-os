from __future__ import annotations

import os
import re
from typing import Any

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════

_BRAND = "Echo"  # Implementation note.

# Per-agent brand override
# -----------------------
#
# In a multi-agent team the default brand "Echo" is wrong for
# sub-agents · e.g. when the Coder agent's underlying Claude leaks
# "I'm Claude" we want to rewrite it to "I'm Coder" (the agent's
# display_name), not "I'm Echo". Hardcoding one brand for the whole
# filter caused every team member to self-identify as the product
# mascot · breaking roster awareness entirely.
#
# ``filter_text`` now accepts an optional ``agent`` (or ``brand``) and
# threads its display_name into the two Chinese / English identity
# assertion rules. The vendor-attribution composite rules still use
# the global product brand · those replacements describe the *project*
# ("built by the Echo team"), not the *speaker*.

# Each rule = (compiled regex, replacement_template). Order matters:
# Case-insensitive for English; Chinese is literal.
#
# Replacements that should adapt to the speaking agent use the
# ``{brand}`` placeholder · plain replacements are literal strings.
_VENDOR_ALTS_EN = (
    r"Anthropic|OpenAI|Moonshot(?:\s*AI)?|DeepSeek|MiniMax|"
    r"Google(?:\s*DeepMind)?|Microsoft|Meta|Alibaba|"
    # Hyperscaler providers · AWS occasionally shows up in model
    # outputs via Bedrock / Claude-on-AWS references · they're NOT
    # the builder of this agent and must be scrubbed like other
    # (leftover after Anthropic was scrubbed but AWS wasn't).
    r"AWS|Amazon(?:\s*Web\s*Services)?|Amazon\s*Bedrock|Bedrock|"
    # More 2025 providers worth covering · Groq hosts Llama, xAI
    # ships Grok, 01.AI trains Yi, Cohere / Mistral / Baichuan /
    # Zhipu / Stability the usual suspects.
    r"Groq|xAI|Cohere|Mistral(?:\s*AI)?|Baichuan(?:\s*AI)?|Zhipu|"
    r"01\.AI|Stability(?:\s*AI)?"
)

_VENDOR_ALTS_ZH = (
    r"Anthropic|OpenAI|Moonshot\s*AI|月之暗面(?:科技(?:有限公司)?)?|"
    r"深度求索|DeepSeek|MiniMax|Google|谷歌|智谱(?:AI|清言)?|"
    r"阿里(?:巴巴)?|字节跳动|百度|"
    r"AWS|亚马逊(?:云服务)?|Amazon|Bedrock|"
    r"Groq|xAI|Cohere|Mistral|百川(?:智能)?|零一万物"
)

_MODEL_ALTS = (
    r"Claude|Kimi|ChatGPT|GPT[\- ]?[\d.]*|Gemini|GLM[\- ]?[\d.]*|"
    r"MiniMax|DeepSeek|Qwen|通义\w*|文心\w*|"
    # 2025 additions · also sometimes leak as "I'm Yi" / "I'm Grok"
    r"Yi[\- ]?\d*|Grok[\- ]?\d*|Llama[\- ]?\d*|LLaMA[\- ]?\d*|"
    r"Baichuan[\- ]?\d*|Mistral[\- ]?\w*"
)

# Rules are applied **in order**. Composite / high-context rules FIRST
# (so "an AI assistant made by Anthropic" is consumed whole before the
# "made by Anthropic" atomic rule gets a chance). Atomic rules AFTER
# handle any residue.
_IDENTITY_RULES: list[tuple[re.Pattern[str], str]] = [
    # ── composite: the whole identity sentence ─────────
    # English: "... an AI assistant made by X ..." → whole clause
    # Replacement deliberately has NO leading brand name — the atomic
    # "I'm Claude" rule below will add the brand once, so we avoid the
    # "I'm Echo, Echo, a biomimetic..." double-brand bug.
    (
        re.compile(
            r"\b(?:an?\s+)?AI\s+(?:assistant|model)\s+"
            r"(?:developed|made|built|created|trained)\s+by\s+"
            rf"(?:{_VENDOR_ALTS_EN})[\w \-]*",
            re.IGNORECASE,
        ),
        "a biomimetic agent OS",
    ),
    (
        re.compile(
            rf"一个?\s*由\s*(?:{_VENDOR_ALTS_ZH})[^,，。.!?；;]*?"
            r"(?:开发|训练|制作|创建|构建)\s*的?\s*(?:AI|人工智能)[^,，。.!?；;]*",
            re.IGNORECASE,
        ),
        "仿生 agent OS",
    ),
    (
        re.compile(
            rf"由\s*(?:{_VENDOR_ALTS_ZH})[^,，。.!?；;]*?"
            r"(?:开发|训练|制作|创建|构建)",
            re.IGNORECASE,
        ),
        "由 Echo 团队构建",
    ),
    # English: "developed by X" atomic (when not wrapped in the
    # composite rule above — e.g. orphan usage).
    (
        re.compile(
            rf"\b(?:developed|made|built|created|trained)\s+by\s+"
            rf"(?:{_VENDOR_ALTS_EN})(?:\s+[A-Za-z]+)?",
            re.IGNORECASE,
        ),
        "built by the Echo team",
    ),
    # ── identity assertion (Chinese) ───────────────────
    # the vendor name, which let hedged self-identifications through like
    #
    # ``{brand}`` is substituted at filter time with the speaking agent's
    # display_name (falls back to the global product brand).
    (
        re.compile(
            rf"我[^，。,.;:!?\n]{{0,6}}是\s*(?:{_MODEL_ALTS})[\w\-]*",
            re.IGNORECASE,
        ),
        "我是 {brand}",
    ),
    # ── identity assertion (English) ───────────────────
    # Same hedge-tolerance for English: "I'm just Claude" / "I am really
    # Claude" / "I'm actually GPT-4" all need to get caught.
    (
        re.compile(
            rf"\bI[' ]?m\s+(?:just|only|really|actually|still|essentially|basically)?\s*(?:an?\s+)?(?:{_MODEL_ALTS})[\w\- ]*",
            re.IGNORECASE,
        ),
        "I'm {brand}",
    ),
    (
        re.compile(
            rf"\bI\s+am\s+(?:just|only|really|actually|still|essentially|basically)?\s*(?:an?\s+)?(?:{_MODEL_ALTS})[\w\- ]*",
            re.IGNORECASE,
        ),
        "I am {brand}",
    ),
    (
        re.compile(
            r"\bI[' ]?m\s+(?:an?\s+)?AI\s+assistant\b",
            re.IGNORECASE,
        ),
        "I'm {brand}",
    ),
]


# ═══════════════════════════════════════════════════════════
# Lock state · env + runtime flags
# ═══════════════════════════════════════════════════════════


# Runtime override · admin-set (persisted in data/identity_lock.json).
# None = defer to env var; True = force locked; False = force unlocked.
_RUNTIME_OVERRIDE: bool | None = None


def set_runtime_lock(locked: bool | None) -> None:
    """Admin API · set the runtime identity-lock override.

    * ``True``  — force-lock regardless of env
    * ``False`` — force-unlock regardless of env
    * ``None``  — clear override, fall back to env var

    Called from ``PUT /api/config/identity-lock`` in ``ui/app.py``.
    """
    global _RUNTIME_OVERRIDE
    _RUNTIME_OVERRIDE = locked


def get_runtime_lock() -> bool | None:
    return _RUNTIME_OVERRIDE


def _env_lock_enabled() -> bool:
    """Runtime override wins. Else read env flag. Default: locked.

    Env values that UNLOCK (all case-insensitive):
        ECHO_IDENTITY_LOCK=0 | false | off | no
    """
    if _RUNTIME_OVERRIDE is not None:
        return _RUNTIME_OVERRIDE
    v = os.environ.get("ECHO_IDENTITY_LOCK", "").strip().lower()
    return v not in {"0", "false", "off", "no"}


def is_locked(
    session: Any = None,
    user_message: str | None = None,
) -> bool:
    """Return True if the identity filter should apply for this turn.

    Three override points (any ONE that flags "unlocked" wins):

    - Env ``ECHO_IDENTITY_LOCK=0``
    - ``session.metadata["identity_lock_override"] = False``
    - User prompt starts with ``/raw`` (debug escape hatch · surfaces
      vendor truthfully for the current turn only)
    """
    if not _env_lock_enabled():
        return False
    if session is not None:
        meta = getattr(session, "metadata", None) or {}
        if meta.get("identity_lock_override") is False:
            return False
    return not (user_message and user_message.strip().startswith("/raw"))


# ═══════════════════════════════════════════════════════════
# Public · filter
# ═══════════════════════════════════════════════════════════


def _resolve_brand(agent: Any = None, brand: str | None = None) -> str:
    """Pick the self-identification brand for this rewrite.

    Precedence:
      1. explicit ``brand`` arg (caller-supplied string, highest trust)
      2. ``agent.display_name`` (when agent passed)
      3. ``agent.agent_id``      (display_name might not be set for ad-hoc)
      4. ``_BRAND``              (global product fallback)

    Never returns empty · empty display_name on agent falls through
    to the next candidate rather than producing "I'm ." artefacts.
    """
    for cand in (brand, _display_of(agent), _id_of(agent), _BRAND):
        if isinstance(cand, str) and cand.strip():
            return cand.strip()
    return _BRAND


def _display_of(agent: Any) -> str | None:
    if agent is None:
        return None
    v = getattr(agent, "display_name", None)
    return v if isinstance(v, str) else None


def _id_of(agent: Any) -> str | None:
    if agent is None:
        return None
    v = getattr(agent, "agent_id", None)
    return v if isinstance(v, str) else None


def filter_text(
    text: str,
    *,
    session: Any = None,
    user_message: str | None = None,
    agent: Any = None,
    brand: str | None = None,
) -> str:
    if not text or not isinstance(text, str):
        return text
    if not is_locked(session=session, user_message=user_message):
        return text
    eff_brand = _resolve_brand(agent=agent, brand=brand)
    out = text
    for pattern, replacement in _IDENTITY_RULES:
        # ``{brand}`` placeholder expands per-call. Non-templated
        # replacements (vendor attribution) pass through str.format
        # unchanged because they have no ``{...}`` segments.
        if "{brand}" in replacement:
            out = pattern.sub(replacement.format(brand=eff_brand), out)
        else:
            out = pattern.sub(replacement, out)
    if eff_brand.lower() != _BRAND.lower():
        out = re.sub(
            r"我是\s*Echo\b",
            f"我是 {eff_brand}",
            out,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            r"\bI(?:'|’)?m\s+Echo\b",
            f"I'm {eff_brand}",
            out,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            r"\bI\s+am\s+Echo\b",
            f"I am {eff_brand}",
            out,
            flags=re.IGNORECASE,
        )
    return out


__all__ = ["filter_text", "is_locked"]
