"""Echo Mix — a mixture-of-agents virtual model for the OpenAI gateway.

Exposes ``echo-mix`` as a selectable model on ``/v1/chat/completions``.
When chosen, the request is answered by a *mixture of agents* instead of a
single model — the pattern popularized by Together AI, Nous Hermes' presets,
and Sakana Fugu:

  1. **Proposers** — N reference models each draft an answer to the user's
     message INDEPENDENTLY and IN PARALLEL. Proposers run as pure LLM calls
     with **no tool access** (``_direct_llm_fallback`` never touches the
     planner/tools), so they can't act — they only advise. Diversity comes
     from (a) different models when a pool is configured and (b) a distinct
     reasoning *lens* injected per proposer (so even a single-model
     deployment gets an ensemble).
  2. **Aggregator** — one model sees the conversation PLUS the proposers'
     drafts (injected as a trailing system message) and synthesizes the
     final answer. The aggregator runs a NORMAL turn (``_run_chat``), so it
     keeps **full tool access** — it can verify/extend the drafts, not just
     blend them.

Config (all optional, env-driven so it works on any deployment):
  - ``ECHO_MIX_PROPOSERS``  comma-separated model ids for the proposer
    pool (e.g. ``"gpt-5.5,claude-opus-4-8,gemini-3.1-pro"``). Unset → run
    ``ECHO_MIX_N`` proposers on the planner-default model, diversified by
    lens.
  - ``ECHO_MIX_AGGREGATOR``  model id for the aggregator. Unset → planner
    default.
  - ``ECHO_MIX_N``  proposer count when no explicit pool (default 3).

When neither the preset nor the env sets a pool, Mix infers one from the
operator's declared cost tiers in ``custom_models.json`` (see
``_read_tagged_catalog``): the draft stage draws on the cheap ``economy`` /
``balanced`` entries, and the aggregator is picked complexity-aware — a
complex (``performance``-verdict) request draws the strong ``performance``
tier, a simple one prefers ``balanced`` and only escalates upward. Explicit
config always wins; a catalog with no tags falls back to the planner-default
behaviour above.

Degrades safely: if every proposer fails (or no router is configured), it
falls back to a single normal turn — never errors out.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.platform.models import ParsedIntent

from .context_manager import _conversation_messages_payload
from .stream_handler import _direct_llm_fallback_with_usage

_log = logging.getLogger(__name__)

# The selectable virtual-model id. ``echo-mix:<preset>`` is also accepted.
MIX_MODEL_ID = "echo-mix"

# Per-proposer reasoning lenses — give an ensemble built from ONE model
# genuine diversity (different angle of attack per draft). Cycled across
# proposers; harmless when a multi-model pool already supplies diversity.
_LENSES: tuple[str, ...] = (
    "Answer directly and precisely; prioritize correctness above all.",
    "Reason step by step and surface edge cases or failure modes others miss.",
    "Take a creative, alternative angle; question the obvious assumptions.",
    "Be rigorous and skeptical; double-check every claim before stating it.",
    "Be concise and practical; focus on what actually matters to the user.",
)

_DEFAULT_N = 3
_MAX_PROPOSERS = 6

# ── Execution-intent markers ─────────────────────────────────
#
# MoA proposers are draft-only advisors with no tools. On a request whose
# whole point is to ACT — write / build / deploy / fix something — their
# drafts are pure latency + tokens: the aggregator has to do the real work
# either way, and the drafts can even anchor it into restating rather than
# executing. So requests carrying an execution verb skip the proposer stage
# and run the single full tool-enabled turn directly. Pure analysis/synthesis
# requests keep the full mixture (multiple draft perspectives genuinely help
# a synthesis-only answer). Deliberately conservative — a missed verb only
# means the mixture still runs, which is the pre-existing behaviour.

_EXECUTION_VERBS_ZH: tuple[str, ...] = (
    "写",
    "创建",
    "建立",
    "生成",
    "修改",
    "编辑",
    "更新",
    "删除",
    "构建",
    "编译",
    "部署",
    "运行",
    "执行",
    "修复",
    "安装",
    "配置",
    "启动",
    "停止",
    "重启",
    "重构",
    "迁移",
    "提交",
    "推送",
    "克隆",
    "下载",
    "上传",
    "初始化",
    "搜索",
    "查找",
    "搭建",
    "实现",
    "开发",
)

_EXECUTION_VERBS_EN: tuple[str, ...] = (
    r"\bwrite\b",
    r"\bcreate\b",
    r"\bbuild\b",
    r"\bdeploy\b",
    r"\brun\b",
    r"\bexecute\b",
    r"\bfix\b",
    r"\brepair\b",
    r"\binstall\b",
    r"\bsetup\b",
    r"\bconfigure\b",
    r"\bgenerate\b",
    r"\brefactor\b",
    r"\bmodify\b",
    r"\bupdate\b",
    r"\bremove\b",
    r"\bdelete\b",
    r"\bcompile\b",
    r"\bclone\b",
    r"\bcommit\b",
    r"\bpush\b",
    r"\bdownload\b",
    r"\bupload\b",
    r"\binit\b",
    r"\bscaffold\b",
    r"\bstart\b",
    r"\bstop\b",
    r"\brestart\b",
)


def _skip_proposers_reason(intent: Any) -> str | None:
    """Return a skip reason when the request is clearly execution-oriented.

    ``None`` → keep the full proposer stage. The returned string is surfaced
    in the ``echo.mix`` metadata so a client can see why proposers were
    skipped (e.g. ``"execution_intent"``).
    """
    goal = ""
    if isinstance(intent, ParsedIntent):
        goal = intent.normalized_goal or intent.raw or ""
    goal = (goal or "").strip()
    if not goal:
        return None
    low = goal.lower()
    if any(verb in goal for verb in _EXECUTION_VERBS_ZH):
        return "execution_intent"
    if any(re.search(pattern, low) for pattern in _EXECUTION_VERBS_EN):
        return "execution_intent"
    return None


# Proposers are draft-only advisors with no tool access (see module
# docstring) — they don't need the ~131K-token ceiling a full agentic
# turn gets. Left uncapped, up to _MAX_PROPOSERS concurrent calls each
# had no token ceiling at all, and ``fut.result()`` had no timeout, so
# one slow/hung proposer stalled the whole mix request for as long as
# the model SDK's own default (~10 min) while holding the caller's
# rate-limit slot. Both are configurable so a deployment with slower
# models isn't forced into these defaults.
_PROPOSER_MAX_TOKENS = int(os.environ.get("ECHO_MIX_PROPOSER_MAX_TOKENS") or 4096)
_PROPOSER_TIMEOUT_SECONDS = float(os.environ.get("ECHO_MIX_PROPOSER_TIMEOUT_SECONDS") or 45.0)


def is_mix_model(model: Any) -> bool:
    """True if ``model`` selects the Mix virtual model."""
    if not isinstance(model, str):
        return False
    name = model.strip().lower()
    return name == MIX_MODEL_ID or name.startswith(MIX_MODEL_ID + ":")


def mix_model_ids() -> list[str]:
    """Virtual-model ids to advertise on /v1/models."""
    return [MIX_MODEL_ID]


def _config_path() -> Path:
    return Path(os.path.expanduser("~/.echo/mix_config.json"))


def load_mix_config() -> dict[str, Any]:
    """User-configured Mix preset (proposer pool / aggregator / count).

    Persisted by the UI via PUT /api/mix-config. Best-effort: a missing or
    malformed file yields {} so env / defaults take over. Resolution order is
    UI config → env → built-in default.
    """
    try:
        data = json.loads(_config_path().read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_mix_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate + persist the Mix preset; returns the cleaned config."""
    proposers = [str(m).strip() for m in (cfg.get("proposers") or []) if str(m or "").strip()][
        :_MAX_PROPOSERS
    ]
    try:
        n = int(cfg.get("n") or _DEFAULT_N)
    except (TypeError, ValueError):
        n = _DEFAULT_N
    clean = {
        "proposers": proposers,
        "aggregator": str(cfg.get("aggregator") or "").strip(),
        "n": max(1, min(_MAX_PROPOSERS, n)),
    }
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    return clean


def _proposer_count() -> int:
    cfg_n = load_mix_config().get("n")
    if isinstance(cfg_n, int) and cfg_n > 0:
        return max(1, min(_MAX_PROPOSERS, cfg_n))
    raw = (os.environ.get("ECHO_MIX_N") or "").strip()
    if raw.isdigit():
        return max(1, min(_MAX_PROPOSERS, int(raw)))
    return _DEFAULT_N


def _proposer_pool() -> list[str]:
    cfg_pool = load_mix_config().get("proposers")
    if isinstance(cfg_pool, list) and cfg_pool:
        return [str(m).strip() for m in cfg_pool if str(m or "").strip()][:_MAX_PROPOSERS]
    raw = (os.environ.get("ECHO_MIX_PROPOSERS") or "").strip()
    if raw:
        models = [m.strip() for m in raw.split(",") if m.strip()]
        return models[:_MAX_PROPOSERS]
    # No explicit pool → infer from the operator's declared cost tiers.
    tagged = _tagged_proposer_pool()
    if tagged:
        return tagged
    return []


def _aggregator_model(intent: Any = None) -> str:
    cfg_agg = load_mix_config().get("aggregator")
    if isinstance(cfg_agg, str) and cfg_agg.strip():
        return cfg_agg.strip()
    env_agg = (os.environ.get("ECHO_MIX_AGGREGATOR") or "").strip()
    if env_agg:
        return env_agg
    # No explicit aggregator → infer from the declared cost tiers,
    # complexity-aware: complex requests draw performance, simple ones
    # prefer balanced.
    return _tagged_aggregator_model(_complexity_flag(intent)) or ""


def _read_tagged_catalog() -> dict[str, list[str]]:
    """Group custom-model ids by their ``tier`` tag (economy/balanced/performance).

    Only entries with an explicit tag participate — an untagged entry carries
    no cost signal and can't be placed in a tier. Best-effort: a missing or
    malformed catalog yields all-empty groups (Mix then keeps the planner
    default). Mirrors the cheap-routing picker's reading of the same field.
    """
    grouped: dict[str, list[str]] = {"economy": [], "balanced": [], "performance": []}
    try:
        from runtime.platform.models.custom_model_flags import read_custom_models

        data = read_custom_models()
    except Exception:  # noqa: BLE001 — best-effort, never break the mix turn
        return grouped
    if not isinstance(data, dict):
        return grouped
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        tier = str(entry.get("tier") or "").strip().lower()
        if tier not in grouped:
            continue
        model_id = str(entry.get("id") or entry.get("name") or "").strip()
        if not model_id:
            raw_models = entry.get("models")
            if isinstance(raw_models, list) and raw_models:
                model_id = str(raw_models[0]).strip()
        if model_id:
            grouped[tier].append(model_id)
    return grouped


def _tagged_proposer_pool() -> list[str]:
    """Proposer pool inferred from custom_models.json cost tiers.

    ``economy`` entries first, ``balanced`` as the fallback — the draft stage
    only wants cheap models, so ``performance``-tagged ones are deliberately
    NOT proposers. Capped at ``_MAX_PROPOSERS`` like an explicit pool.
    """
    grouped = _read_tagged_catalog()
    pool = grouped["economy"] + grouped["balanced"]
    return pool[:_MAX_PROPOSERS]


def _complexity_flag(intent: Any) -> bool | None:
    """Map an intent to the aggregator's complexity signal.

    ``True`` → a complex (``performance``-verdict) request, ``False`` → a
    simple (``local``/``value``-verdict) one, ``None`` → no signal available
    (keep the default fallback). Reuses the same ``turn_complexity``
    classifier the chat fast-path and sub-agent routing use, so the inferred
    aggregator aligns with the rest of the system. Best-effort: a missing
    goal or an import failure yields ``None`` rather than breaking the turn.
    """
    if intent is None:
        return None
    goal = (getattr(intent, "normalized_goal", "") or getattr(intent, "raw", "") or "").strip()
    if not goal:
        return None
    try:
        from runtime.core.cerebrum.turn_complexity import estimate_turn_complexity

        verdict = estimate_turn_complexity(goal)
    except Exception:  # noqa: BLE001 — best-effort; default when unavailable
        return None
    return verdict == "performance"


def _tagged_aggregator_model(complex_turn: bool | None = None) -> str | None:
    """Aggregator inferred from custom_models.json cost tiers.

    Complexity-aware when ``complex_turn`` is given (see ``_complexity_flag``):
      * ``True``  — a complex request draws the strong ``performance`` tier
        and NEVER demotes to ``balanced`` (matching turn_complexity's
        "never demote" chain); no performance tier → ``None`` → planner
        default.
      * ``False`` — a simple request prefers ``balanced`` and only escalates
        upward to ``performance`` when no balanced model is declared. The
        aggregator never draws the cheap ``economy`` tier — it must stay
        stronger than the drafters to be worth the mixture.
      * ``None``  — no signal: keep the historical performance→balanced
        fallback.
    Deterministic sorted pick so the choice is stable across calls.
    """
    grouped = _read_tagged_catalog()
    tiers: tuple[str, ...]
    if complex_turn is True:
        tiers = ("performance",)
    elif complex_turn is False:
        tiers = ("balanced", "performance")
    else:
        tiers = ("performance", "balanced")
    for tier in tiers:
        if grouped[tier]:
            return sorted(grouped[tier])[0]
    return None


def _proposer_specs(requested_model: str) -> list[tuple[str, str]]:
    """Resolve ``(model, lens)`` pairs for the proposers.

    ``model == ""`` means "use the planner default model" (single-model
    deployments still get an ensemble via distinct lenses).
    """
    models = _proposer_pool() or [""] * _proposer_count()
    return [(m, _LENSES[i % len(_LENSES)]) for i, m in enumerate(models)]


def _proposer_intent(intent: ParsedIntent, lens: str) -> ParsedIntent:
    """Derive a proposer intent that prepends a reasoning ``lens``."""
    convo = [{"role": "system", "content": lens}, *_conversation_messages_payload(intent)]
    return intent.model_copy(
        update={
            "user_context": {
                **(intent.user_context or {}),
                "conversation_messages": convo,
            }
        }
    )


def _aggregator_intent(intent: ParsedIntent, drafts: list[str]) -> ParsedIntent:
    """Derive the aggregator intent with proposer drafts injected."""
    convo = list(_conversation_messages_payload(intent))
    convo.append({"role": "system", "content": _format_proposals(drafts)})
    return intent.model_copy(
        update={
            "user_context": {
                **(intent.user_context or {}),
                "conversation_messages": convo,
                "mix_proposals": list(drafts),
            }
        }
    )


def _format_proposals(drafts: list[str]) -> str:
    lines = [
        "You are the AGGREGATOR in a mixture-of-agents system. Several "
        "reference models independently drafted answers to the user's latest "
        "message (below). Treat them as ADVICE, not ground truth. Your job is "
        "to deliver the user's request to COMPLETION: synthesize a final "
        "answer that is more correct, complete, and useful than any single "
        "draft, and — when the request asks for an action (writing files, "
        "running commands, checking facts) — actually perform it with your "
        "tools. Drafts are starting points, NOT the deliverable; do not just "
        "restate them. Resolve disagreements by reasoning, and verify claims "
        "with tools when it matters. Never mention the drafts, the references, "
        "or this process — just give the user the best outcome.",
        "",
    ]
    for i, draft in enumerate(drafts, 1):
        text = (draft or "").strip()
        if text:
            lines.append(f"--- Draft {i} ---")
            lines.append(text)
            lines.append("")
    return "\n".join(lines).strip()


def run_mix_chat(
    stack: Any,
    intent: ParsedIntent,
    requested_model: str,
    default_arm: str,
    *,
    actor: str | None,
    agent: Any,
    run_chat: Any,
    optimizer: Any = None,
) -> dict[str, Any]:
    """Answer ``intent`` via mixture-of-agents and return a chat.completion.

    ``run_chat`` is injected (the gateway's ``_run_chat``) to avoid a circular
    import; it runs the aggregator as a normal, fully tool-enabled turn.
    """
    specs = _proposer_specs(requested_model)

    # Execution-oriented request → the proposer stage would be pure latency
    # and tokens (draft-only, no tools), and could even anchor the aggregator
    # into restating instead of acting. Skip straight to the single full
    # tool-enabled turn on the aggregator model.
    skip_reason = _skip_proposers_reason(intent)
    if skip_reason:
        aggregator_model = _aggregator_model(intent)
        result = run_chat(
            stack,
            intent,
            aggregator_model,
            default_arm,
            optimizer=optimizer,
            actor=actor,
            agent=agent,
        )
        if isinstance(result, dict):
            result.setdefault("echo", {})["mix"] = {
                "skipped_proposers": skip_reason,
                "proposers": 0,
                "drafts_used": 0,
                "aggregator_model": aggregator_model or "default",
            }
            result["model"] = requested_model
        return result

    # ── Stage 1: proposers — parallel, NO tools ──────────────
    def _one(spec: tuple[str, str]) -> str | None:
        model, lens = spec
        try:
            reply, _usage = _direct_llm_fallback_with_usage(
                stack,
                _proposer_intent(intent, lens),
                agent,
                model=(model or None),
                max_tokens_cap=_PROPOSER_MAX_TOKENS,
            )
            return reply
        except Exception as exc:  # noqa: BLE001 — one proposer failing must not sink the turn
            _log.warning("mix proposer (model=%r) failed: %s", model, exc)
            return None

    drafts: list[str] = []
    workers = max(1, min(len(specs), _MAX_PROPOSERS))
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        # copy_context() per task so ContextVars (actor, session) propagate
        # into the worker threads — one fresh copy each (a context can be
        # entered only once).
        futures = [pool.submit(contextvars.copy_context().run, _one, spec) for spec in specs]
        # ``wait(..., timeout=N)`` bounds the TOTAL time stage 1 can hold
        # up the request, unlike a per-future ``fut.result(timeout=N)``
        # loop — that would still sum to N * len(futures) in the worst
        # case (each iteration re-waiting up to N seconds even though
        # every proposer is running concurrently in the background).
        done, not_done = wait(futures, timeout=_PROPOSER_TIMEOUT_SECONDS)
        if not_done:
            _log.warning(
                "mix: %d/%d proposer(s) still running after %.0fs, dropping their drafts",
                len(not_done),
                len(futures),
                _PROPOSER_TIMEOUT_SECONDS,
            )
        # Iterate ``futures`` (lens order) rather than the ``done`` set —
        # set iteration order varies run-to-run and would shuffle the
        # drafts the aggregator sees, making Mix output nondeterministic.
        for fut in futures:
            if fut not in done:
                continue
            try:
                reply = fut.result()
            except Exception:  # noqa: BLE001 — defensive; _one already guards
                reply = None
            if reply and reply.strip():
                drafts.append(reply.strip())
    finally:
        # A timed-out proposer's thread can't be cancelled (Python
        # threads aren't preemptible) — shut down without blocking on
        # it so the timeout above actually bounds request latency; the
        # thread finishes on its own and its result is simply unused.
        pool.shutdown(wait=False)

    aggregator_model = _aggregator_model(intent)

    # ── Stage 2: aggregator — full turn, WITH tools ──────────
    if not drafts:
        # Nothing usable to synthesize → just run one normal turn.
        result = run_chat(
            stack,
            intent,
            aggregator_model,
            default_arm,
            optimizer=optimizer,
            actor=actor,
            agent=agent,
        )
        if isinstance(result, dict):
            result.setdefault("echo", {})["mix"] = {
                "proposers": len(specs),
                "drafts_used": 0,
                "degraded": True,
            }
            result["model"] = requested_model
        return result

    result = run_chat(
        stack,
        _aggregator_intent(intent, drafts),
        aggregator_model,
        default_arm,
        optimizer=optimizer,
        actor=actor,
        agent=agent,
    )
    if isinstance(result, dict):
        result.setdefault("echo", {})["mix"] = {
            "proposers": len(specs),
            "drafts_used": len(drafts),
            "aggregator_model": aggregator_model or "default",
            "proposer_models": [m or "default" for m, _ in specs],
            "degraded": False,
        }
        result["model"] = requested_model
    return result


def mix_sse_frames(result: dict[str, Any], model: str):
    """Wrap a finished Mix completion as OpenAI-standard streaming SSE.

    Mix is computed non-streamed (proposers must finish before the aggregator
    starts); for ``stream=true`` clients we still emit a valid chunk sequence.
    """
    try:
        content = result["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        content = ""
    cid = result.get("id") or f"chatcmpl-{uuid4().hex[:16]}"
    created = result.get("created") or int(time.time())
    base = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model}

    def _frame(delta: dict[str, Any], finish: str | None) -> str:
        payload = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    yield _frame({"role": "assistant"}, None)
    if content:
        yield _frame({"content": content}, None)
    yield _frame({}, "stop")
    yield "data: [DONE]\n\n"
