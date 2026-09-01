"""Provider Capability Auto-Detection.

The capability surface of a provider shifts between releases
(function-calling lands, structured output regresses, vision gets
toggled per model). Static declarations drift; runtime probes stay
honest. This module sends a minimal canary request at startup to
discover which capabilities are actually available, rather than
relying solely on static declarations.

This module provides:

``probe_provider(router, *, model, timeout_s)``
    Send a minimal canary request to ``router`` and return a
    ``ProviderCapabilities`` instance reflecting what actually worked.
    Results are cached in memory (and optionally on disk at
    ``~/.echo/provider_caps.json``) so subsequent calls are free.

``get_cached_capabilities(provider_key)``
    Return the last probed capabilities for a provider key, or
    ``None`` if not yet probed.

``clear_capability_cache()``
    Flush the in-memory cache (useful in tests).

Cache key
---------
The cache key is ``f"{provider_name}:{model}"`` where
``provider_name`` comes from ``router.provider_name`` (the
``Provider`` mixin) or ``type(router).__name__`` as a fallback.

Disk cache
----------
Results are persisted to ``~/.echo/provider_caps.json`` as a
flat JSON dict keyed by the same cache key. The file is written
atomically (temp + rename). On read, entries older than
``CACHE_TTL_HOURS`` (default 24) are treated as stale and re-probed.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from .provider import ProviderCapabilities

_LOG = logging.getLogger("echo.eyes.capability_probe")

# ── Cache TTL ────────────────────────────────────────────────
CACHE_TTL_HOURS: float = 24.0

# ── In-memory cache ──────────────────────────────────────────
_CACHE: dict[str, ProviderCapabilities] = {}
_CACHE_TS: dict[str, float] = {}  # unix timestamp of last probe
_CACHE_LOCK = threading.Lock()


def _disk_cache_path() -> Path:
    return Path.home() / ".echo" / "provider_caps.json"


def _load_disk_cache() -> dict[str, Any]:
    path = _disk_cache_path()
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):  # noqa: BLE001 — capability cache read failed; return empty dict
        pass
    return {}


def _save_disk_cache(data: dict[str, Any]) -> None:
    path = _disk_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("capability_probe: disk cache write failed: %s", exc)


def _caps_to_dict(caps: ProviderCapabilities) -> dict[str, Any]:
    return {
        "supports_vision": caps.supports_vision,
        "supports_tool_use": caps.supports_tool_use,
        "supports_streaming": caps.supports_streaming,
        "supports_prompt_cache": caps.supports_prompt_cache,
        "supports_structured_output": caps.supports_structured_output,
        "default_model": caps.default_model,
        "pricing_hint": caps.pricing_hint,
        "extra": dict(caps.extra),
    }


def _caps_from_dict(raw: dict[str, Any]) -> ProviderCapabilities:
    return ProviderCapabilities(
        supports_vision=bool(raw.get("supports_vision", False)),
        supports_tool_use=bool(raw.get("supports_tool_use", False)),
        supports_streaming=bool(raw.get("supports_streaming", False)),
        supports_prompt_cache=bool(raw.get("supports_prompt_cache", False)),
        supports_structured_output=bool(raw.get("supports_structured_output", False)),
        default_model=str(raw.get("default_model", "")),
        pricing_hint=str(raw.get("pricing_hint", "")),
        extra=dict(raw.get("extra") or {}),
    )


def _provider_key(router: Any, model: str) -> str:
    name = getattr(router, "provider_name", None) or type(router).__name__
    return f"{name}:{model}"


def get_cached_capabilities(provider_key: str) -> ProviderCapabilities | None:
    """Return in-memory cached capabilities for ``provider_key``, or None."""
    with _CACHE_LOCK:
        return _CACHE.get(provider_key)


def clear_capability_cache() -> None:
    """Flush the in-memory capability cache (useful in tests)."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE_TS.clear()


def probe_provider(
    router: Any,
    *,
    model: str = "",
    timeout_s: float = 5.0,
    force: bool = False,
) -> ProviderCapabilities:
    """Probe ``router`` and return its runtime capabilities.

    The probe is cached in memory and on disk. Subsequent calls with
    the same ``(router, model)`` pair return the cached result
    immediately unless ``force=True`` or the cache is stale.

    Parameters
    ----------
    router:
        Any ``ModelRouter`` (or ``Provider``-mixin) instance.
    model:
        Model id to probe. Falls back to ``router.default_model`` or
        ``router.capabilities.default_model`` when empty.
    timeout_s:
        Per-probe HTTP timeout in seconds.
    force:
        Re-probe even if a fresh cache entry exists.

    Returns
    -------
    ProviderCapabilities
        Detected capabilities. On any probe failure the method returns
        the router's static ``capabilities`` declaration (or a
        zero-capability instance) so callers always get a usable result.
    """
    # Resolve model
    if not model:
        model = (
            getattr(router, "default_model", None)
            or getattr(getattr(router, "capabilities", None), "default_model", None)
            or ""
        )

    key = _provider_key(router, model)

    # Check in-memory cache first.
    with _CACHE_LOCK:
        if not force and key in _CACHE:
            age_h = (time.time() - _CACHE_TS.get(key, 0)) / 3600
            if age_h < CACHE_TTL_HOURS:
                return _CACHE[key]

    # Check disk cache.
    if not force:
        disk = _load_disk_cache()
        entry = disk.get(key)
        if isinstance(entry, dict):
            ts = float(entry.get("_probed_at", 0))
            age_h = (time.time() - ts) / 3600
            if age_h < CACHE_TTL_HOURS:
                caps = _caps_from_dict(entry)
                with _CACHE_LOCK:
                    _CACHE[key] = caps
                    _CACHE_TS[key] = ts
                return caps

    # Static declaration as fallback baseline.
    static_caps: ProviderCapabilities = getattr(router, "capabilities", ProviderCapabilities())

    detected = _run_probes(router, model=model, timeout_s=timeout_s, baseline=static_caps)

    # Persist to memory + disk.
    now = time.time()
    with _CACHE_LOCK:
        _CACHE[key] = detected
        _CACHE_TS[key] = now

    disk = _load_disk_cache()
    entry_dict = _caps_to_dict(detected)
    entry_dict["_probed_at"] = now
    disk[key] = entry_dict
    _save_disk_cache(disk)

    _LOG.info(
        "capability_probe: %s → streaming=%s tool_use=%s vision=%s",
        key,
        detected.supports_streaming,
        detected.supports_tool_use,
        detected.supports_vision,
    )
    return detected


# ── Probe helpers ────────────────────────────────────────────


def _run_probes(
    router: Any,
    *,
    model: str,
    timeout_s: float,
    baseline: ProviderCapabilities,
) -> ProviderCapabilities:
    """Run canary probes and merge results with ``baseline``."""
    streaming = _probe_streaming(router, model=model, timeout_s=timeout_s)
    tool_use = _probe_tool_use(router, model=model, timeout_s=timeout_s)
    vision = _probe_vision(router, model=model, timeout_s=timeout_s)
    structured_output = _probe_structured_output(router, model=model, timeout_s=timeout_s)
    system_prompt = _probe_system_prompt(router, model=model, timeout_s=timeout_s)
    reasoning_effort = _probe_reasoning_effort(router, model=model, timeout_s=timeout_s)
    unsupported_fields = sorted(
        {
            field
            for field, value in {
                "streaming": streaming,
                "tool_use": tool_use,
                "vision": vision,
                "json_schema": structured_output,
                "system_prompt": system_prompt,
                "reasoning_effort": reasoning_effort,
            }.items()
            if value is False
        }
    )
    extra = dict(baseline.extra)
    extra["capability_probe"] = {
        "schema": "echo.provider_capability_probe.v1",
        "model": model,
        "streaming": streaming,
        "tool_use": tool_use,
        "vision": vision,
        "json_schema": structured_output,
        "system_prompt": system_prompt,
        "reasoning_effort": reasoning_effort,
        "unsupported_fields": unsupported_fields,
    }

    return ProviderCapabilities(
        supports_streaming=streaming if streaming is not None else baseline.supports_streaming,
        supports_tool_use=tool_use if tool_use is not None else baseline.supports_tool_use,
        supports_vision=vision if vision is not None else baseline.supports_vision,
        # Non-probed caps inherit from static declaration.
        supports_prompt_cache=baseline.supports_prompt_cache,
        supports_structured_output=(
            structured_output
            if structured_output is not None
            else baseline.supports_structured_output
        ),
        default_model=baseline.default_model or model,
        pricing_hint=baseline.pricing_hint,
        extra=extra,
    )


def _probe_streaming(router: Any, *, model: str, timeout_s: float) -> bool | None:
    """Return True if the router can stream, False if not, None on error."""
    stream_fn = getattr(router, "stream", None)
    stream_label = "stream"
    if stream_fn is None:
        from .models import ModelRouter

        call_stream_fn = getattr(router, "call_stream", None)
        call_stream_impl = getattr(type(router), "call_stream", None)
        if call_stream_fn is not None and call_stream_impl is not ModelRouter.call_stream:
            stream_fn = call_stream_fn
            stream_label = "call_stream"
    if stream_fn is None:
        return False
    try:
        from .models import Message, ModelRequest

        req = ModelRequest(
            model=model or "probe",
            messages=[Message(role="user", content="ping")],
            max_tokens=1,
        )
        # Consume at most one token to confirm streaming works.
        gen = stream_fn(req)
        if gen is not None:
            with contextlib.suppress(StopIteration):
                next(iter(gen))
        return True
    except NotImplementedError:
        return False
    except Exception as exc:  # noqa: BLE001
        _LOG.debug(
            "%s probe failed for %s: %s",
            stream_label,
            type(router).__name__,
            exc,
        )
        return None


def _probe_tool_use(router: Any, *, model: str, timeout_s: float) -> bool | None:
    """Return True if the router accepts tool specs, False if not, None on error."""
    call_fn = getattr(router, "call", None)
    if call_fn is None:
        return None
    try:
        from .models import Message, ModelRequest, ToolSpec

        req = ModelRequest(
            model=model or "probe",
            messages=[Message(role="user", content="What tools do you have?")],
            max_tokens=1,
            tools=[
                ToolSpec(
                    name="probe_tool",
                    description="Probe tool for capability detection.",
                )
            ],
        )
        call_fn(req)
        return True
    except NotImplementedError:
        return False
    except TypeError:
        # Router's call() doesn't accept tools kwarg — no tool support.
        return False
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("tool_use probe failed for %s: %s", type(router).__name__, exc)
        return None


def _probe_vision(router: Any, *, model: str, timeout_s: float) -> bool | None:
    """Return True if the router accepts image content blocks, False if not."""
    call_fn = getattr(router, "call", None)
    if call_fn is None:
        return None
    try:
        from .models import Message, ModelRequest

        # Minimal 1×1 transparent PNG as base64.
        _TINY_PNG_B64 = (  # noqa: N806
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
            "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        )
        req = ModelRequest(
            model=model or "probe",
            messages=[
                Message(
                    role="user",
                    content=[
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": _TINY_PNG_B64,
                            },
                        },
                        {"type": "text", "text": "What is this?"},
                    ],
                )
            ],
            max_tokens=1,
        )
        call_fn(req)
        return True
    except NotImplementedError:
        return False
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("vision probe failed for %s: %s", type(router).__name__, exc)
        return None


def _probe_structured_output(router: Any, *, model: str, timeout_s: float) -> bool | None:
    """Return True if the provider can follow a minimal JSON schema canary."""
    call_fn = getattr(router, "call", None)
    if call_fn is None:
        return None
    try:
        from .models import Message, ModelRequest

        req = ModelRequest(
            model=model or "probe",
            messages=[
                Message(
                    role="user",
                    content=(
                        "Return exactly one minified JSON object matching this schema: "
                        '{"ok": boolean}. Use {"ok": true}.'
                    ),
                )
            ],
            max_tokens=16,
            temperature=0.0,
        )
        response = call_fn(req)
        text = str(getattr(response, "text", "") or "").strip()
        parsed = json.loads(text)
        return isinstance(parsed, dict) and isinstance(parsed.get("ok"), bool)
    except NotImplementedError:
        return False
    except json.JSONDecodeError:
        return False
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("structured output probe failed for %s: %s", type(router).__name__, exc)
        return None


def _probe_system_prompt(router: Any, *, model: str, timeout_s: float) -> bool | None:
    """Return True if a system-role message is accepted."""
    call_fn = getattr(router, "call", None)
    if call_fn is None:
        return None
    try:
        from .models import Message, ModelRequest

        req = ModelRequest(
            model=model or "probe",
            messages=[
                Message(role="system", content="You are a probe. Reply with ok."),
                Message(role="user", content="ping"),
            ],
            max_tokens=4,
        )
        call_fn(req)
        return True
    except NotImplementedError:
        return False
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        if "system" in message and ("unsupported" in message or "not support" in message):
            return False
        _LOG.debug("system prompt probe failed for %s: %s", type(router).__name__, exc)
        return None


def _probe_reasoning_effort(router: Any, *, model: str, timeout_s: float) -> bool | None:
    """Return True if the router accepts the reasoning_effort request field."""
    call_fn = getattr(router, "call", None)
    if call_fn is None:
        return None
    try:
        from .models import Message, ModelRequest

        req = ModelRequest(
            model=model or "probe",
            messages=[Message(role="user", content="ping")],
            max_tokens=2048,
            reasoning_effort="low",
        )
        call_fn(req)
        return True
    except NotImplementedError:
        return False
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        if "reasoning" in message and ("unsupported" in message or "not support" in message):
            return False
        _LOG.debug("reasoning probe failed for %s: %s", type(router).__name__, exc)
        return None


__all__ = [
    "CACHE_TTL_HOURS",
    "clear_capability_cache",
    "get_cached_capabilities",
    "probe_provider",
]
