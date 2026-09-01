"""
Invariants router · catalog of the 34-rule constitution and which
functions enforce each rule.

Why this exists
---------------
The safety system in ``runtime.safety.invariants`` ships a family of
decorators (``@enforces``, ``@require``, ``@ensure``, ``@monotonic``,
``@append_only``) that each tag the wrapped function with a
``__enforces__`` tuple of rule ids. Without an introspection endpoint,
operators / the frontend / the security review process have no way
to answer:

    "Which functions enforce rule BDG-I1?"
    "Are there rules nobody enforces?"

Modern agent runtimes ship a similar catalog endpoint for the same
reason — discoverability of the safety surface. We mirror that here.

Discovery strategy
------------------
We deliberately do **NOT** import every module under ``runtime/`` —
that would be slow and would trigger import side effects (HTTP
clients, schedulers, etc.). Instead we walk ``sys.modules`` at
build time and collect every callable whose ``__enforces__`` tuple
is non-empty. Modules that haven't been imported yet contribute
zero rules — that's fine, the request handler treats this as a
*snapshot of currently-loaded code*.

Result is cached in module-level state on first build; ``POST
/api/invariants/refresh`` invalidates and rebuilds. Tests rely on
this contract.

Public API
----------
``create_invariants_router()`` → FastAPI ``APIRouter`` with three
routes:

    GET    /api/invariants                · full catalog
    GET    /api/invariants/{rule_id}      · one rule's enforcers
    POST   /api/invariants/refresh        · invalidate + rebuild cache
"""

from __future__ import annotations

import inspect
import sys
import threading
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

# ═══════════════════════════════════════════════════════════
# Cache · process-wide snapshot
# ═══════════════════════════════════════════════════════════
#
# A single dict mapping rule_id -> list of enforcer dicts. Built
# lazily on first request. Refresh endpoint clears it. The lock
# prevents two concurrent builds from doing duplicate work — readers
# see a consistent snapshot even if a refresh is in flight.

_CACHE: dict[str, list[dict[str, str]]] | None = None
_CACHE_LOCK = threading.Lock()


def _qualname_for(fn: Any) -> str:
    """Best-effort qualified name for a callable.

    Most decorators set ``__qualname__`` via ``functools.wraps`` — but
    some don't (e.g. lambdas, hand-rolled wrappers). Fall back to
    ``__name__`` then ``repr`` so we never raise from this introspection
    path.
    """
    qual = getattr(fn, "__qualname__", None)
    if isinstance(qual, str) and qual:
        return qual
    name = getattr(fn, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return repr(fn)


def _module_for(fn: Any) -> str:
    """Module dotted-path or empty string when undeterminable."""
    mod = getattr(fn, "__module__", None)
    return mod if isinstance(mod, str) else ""


def _walk_loaded_modules() -> dict[str, list[dict[str, str]]]:
    """Scan every module currently in ``sys.modules`` and collect
    callables tagged with a non-empty ``__enforces__`` tuple.

    Returns
    -------
    dict[rule_id, list[{module, qualname}]]
        One entry per rule id seen, with deduplicated enforcer rows.
    """
    rules: dict[str, list[dict[str, str]]] = {}
    seen: set[tuple[str, str, str]] = set()  # (rule_id, module, qualname) dedupe

    # Snapshot module list — sys.modules can mutate during iteration if
    # any of the introspected modules trigger lazy imports. Iterating a
    # tuple snapshot avoids RuntimeError on dict size change.
    module_items = tuple(sys.modules.items())

    for mod_name, module in module_items:
        if module is None:
            continue
        try:
            members = tuple(vars(module).items())
        except (TypeError, AttributeError, OSError):  # noqa: BLE001 — some modules forbid vars()
            continue

        for _attr_name, value in members:
            # Three places ``__enforces__`` can live:
            #   1. the value itself (top-level decorated function)
            #   2. methods on a class (we walk class __dict__ below)
            # We handle (1) here and (2) by recursing into classes.
            #
            # ``getattr`` is wrapped in try/except because some modules
            # (e.g. ``six.MovedModule``, ``anthropic._utils._proxy.LazyProxy``)
            # trigger lazy imports on attribute access — those imports
            # can fail with arbitrary exceptions (third-party SDKs raise
            # custom ``MissingDependencyError`` etc.), and we don't want
            # a single weird third-party module to abort the whole walk.
            try:
                enforces_tuple = getattr(value, "__enforces__", None)
            except Exception:  # noqa: BLE001
                continue
            if enforces_tuple:
                _record(rules, seen, value, enforces_tuple)

            try:
                is_cls = inspect.isclass(value)
            except Exception:  # noqa: BLE001
                continue

            if is_cls:
                # Only walk methods declared on classes that live in
                # this module, otherwise we'd record the same method
                # from every module that imports the class.
                try:
                    if getattr(value, "__module__", None) != mod_name:
                        continue
                    class_members = tuple(vars(value).items())
                except Exception:  # noqa: BLE001
                    continue
                for _meth_name, meth in class_members:
                    try:
                        enforces_tuple = getattr(meth, "__enforces__", None)
                    except Exception:  # noqa: BLE001
                        continue
                    if enforces_tuple:
                        _record(rules, seen, meth, enforces_tuple)

    # Stable order for repeatable snapshots — frontend can diff two
    # successive responses without spurious churn.
    for rid in rules:
        rules[rid].sort(key=lambda e: (e["module"], e["qualname"]))

    return rules


def _record(
    rules: dict[str, list[dict[str, str]]],
    seen: set[tuple[str, str, str]],
    fn: Any,
    rule_ids: Any,
) -> None:
    module = _module_for(fn)
    qualname = _qualname_for(fn)
    # ``rule_ids`` is supposed to be a tuple[str, ...] set by the
    # ``@enforces`` decorator. But sys.modules walks turn up all
    # kinds of exotic objects (unittest.mock Sentinels, proxy
    # objects, deferred imports) whose ``__enforces__`` attribute
    # can be anything. Validate before iterating — one bad object
    # must not break the whole catalog.
    try:
        iterator = iter(rule_ids)
    except TypeError:
        return
    for rid in iterator:
        if not isinstance(rid, str):
            continue
        key = (rid, module, qualname)
        if key in seen:
            continue
        seen.add(key)
        rules.setdefault(rid, []).append(
            {"module": module, "qualname": qualname},
        )


def _build_or_get_cache() -> dict[str, list[dict[str, str]]]:
    """Return the cached catalog, building it on first call."""
    global _CACHE
    with _CACHE_LOCK:
        if _CACHE is None:
            _CACHE = _walk_loaded_modules()
        return _CACHE


def _invalidate_cache() -> None:
    """Drop the snapshot so the next read rebuilds. Used by the
    refresh endpoint."""
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None


# ═══════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════


def create_invariants_router(
    *,
    stack: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    """Build the FastAPI router for the invariants catalog.

    Parameters
    ----------
    stack :
        Reserved for future use (e.g. wiring the stack's already-loaded
        modules into the discovery walk). Currently unused — discovery
        scans ``sys.modules`` directly. Accepted for API symmetry with
        ``create_config_router``.
    """
    require_fastapi(__name__)

    # ``stack`` is intentionally unused for now — see docstring.
    del stack

    router = APIRouter(tags=["invariants"])

    def _auth(request: Request) -> str | None:
        if require_auth and identity_store is None:
            raise HTTPException(401, "auth required")
        from runtime.sensing.gateway.openai_gateway_router import _resolve_actor

        return _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _require_operator(request: Request) -> None:
        from runtime.safety.auth.principal import require_roles

        require_roles(
            request,
            identity_store,
            require_auth,
            ("admin", "operator"),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _format_catalog() -> dict[str, Any]:
        rules_map = _build_or_get_cache()
        rule_ids_sorted = sorted(rules_map.keys())
        rules_list = [{"rule_id": rid, "enforcers": rules_map[rid]} for rid in rule_ids_sorted]
        total_enforcers = sum(len(rules_map[r]) for r in rule_ids_sorted)
        return {
            "rules": rules_list,
            "total_rules": len(rules_list),
            "total_enforcers": total_enforcers,
        }

    @router.get("/api/invariants")
    def api_invariants_list(request: Request) -> dict[str, Any]:
        _auth(request)
        """Catalog of every rule the running process knows about.

        Walks ``sys.modules`` once (then caches) and returns one entry
        per rule id seen on a function's ``__enforces__`` tuple.
        """
        return _format_catalog()

    @router.get("/api/invariants/{rule_id}")
    def api_invariants_detail(request: Request, rule_id: str) -> dict[str, Any]:
        _auth(request)
        """Return enforcers for a single rule, or 404 if unknown.

        Shape matches one element of ``GET /api/invariants``'s
        ``rules`` array, so frontend code can reuse the same renderer
        for both list and detail views.
        """
        rules_map = _build_or_get_cache()
        if rule_id not in rules_map:
            raise HTTPException(404, f"unknown rule_id: {rule_id}")
        return {
            "rule_id": rule_id,
            "enforcers": rules_map[rule_id],
        }

    @router.post("/api/invariants/refresh")
    def api_invariants_refresh(request: Request) -> dict[str, Any]:
        _require_operator(request)
        """Invalidate the cache and rebuild the catalog.

        Useful after a hot-reload pulls new safety-decorated modules
        into ``sys.modules`` — without this the cache would still
        reflect the pre-reload snapshot.
        """
        _invalidate_cache()
        return _format_catalog()

    return router


__all__ = ["create_invariants_router"]
