"""
Config router · identity-lock + providers + custom-models.

Carved out of the monolithic ``runtime/platform/ui/app.py`` (which
owned 48 endpoints in a single 2324-line factory). This router owns
the "configuration surface" that the frontend Settings / ModelPicker
panels speak to:

    GET    /api/config/identity-lock       · privacy-filter state
    PUT    /api/config/identity-lock       · admin toggle
    GET    /api/providers                  · LLM provider capability list
    GET    /api/config/custom-models       · list user-added models
    PUT    /api/config/custom-models/{id}  · upsert + persist + register
    DELETE /api/config/custom-models/{id}  · remove + persist + unregister

Public API is a single factory ``create_config_router(...)``. It
returns a ``ConfigRouter`` thin wrapper that exposes:

    .router           — FastAPI APIRouter to include on the main app
    .custom_models    — live dict, shared with app.py's /api/llm-models
                        merge endpoint so the existing merge logic
                        doesn't need duplicating here

Design notes
------------

* **State ownership moves with the routes.** The ``custom_models``
  dict used to be a local in ``create_app``; now it's owned by the
  router. app.py reads it through the returned wrapper when composing
  the model catalog.
* **Disk format unchanged.** Path and JSON shape stay the same so an
  in-place deploy doesn't lose user config. Integration tests in
  ``tests/test_app_config_endpoints.py`` pin this contract.
* **Dispatcher is optional.** Without a ``stack.planner.router``,
  register/unregister degrade to no-ops with a reason field —
  matches the pre-split behavior. Tests rely on this to exercise
  persistence without needing a full execution stack.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any

from runtime.execution.codex_backend.account import CodexAccountService
from runtime.execution.codex_backend.model_profile import CodexModelPreferenceStore
from runtime.execution.codex_backend.paths import resolve_codex_state_root
from runtime.execution.codex_backend.upstream_update import CodexUpstreamUpdateService
from runtime.platform.models.custom_model_selection import selections_for_entry
from runtime.platform.process.paths import app_paths

try:
    from fastapi import APIRouter, Depends, HTTPException, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    Depends = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi
from runtime.sensing.gateway._config_endpoints import (
    _build_endpoints,
    _ConfigCtx,
)
from runtime.sensing.gateway._config_helpers import (
    _entry_model_id,
    _entry_route_ids,
    _entry_upstreams,
)
from runtime.sensing.gateway._config_models import (
    ConstitutionProfilePutBody,
    ConstitutionProfileResponse,
    CustomModelDeleteResponse,
    CustomModelEntry,
    CustomModelsList,
    CustomModelTestResponse,
    CustomModelUpsertResponse,
    CustomModelUpsertStatus,
    IdentityLockPutBody,
    IdentityLockResponse,
    ProviderCapabilitiesWire,
    ProvidersResponse,
)

# ═══════════════════════════════════════════════════════════
# Wrapper · holds router + shared mutable state
# ═══════════════════════════════════════════════════════════


@dataclass
class ConfigRouter:
    """Bundle returned by ``create_config_router``.

    ╔════════════════════════════════════════════════════════════════════╗
    ║ config_router.py · navigation map (1265 lines).                    ║
    ║                                                                    ║
    ║   §1 ConfigRouter dataclass                       ~L180            ║
    ║   §2 create_config_router (factory)               ~L198            ║
    ║       §2.1 /api/config (root + put)               ~L426            ║
    ║       §2.2 /api/providers                         ~L489            ║
    ║       §2.3 /api/config/custom-models (CRUD)       ~L528            ║
    ║       §2.4 /api/config/local-models/scan|import   ~L650            ║
    ║       §2.5 /api/llm-models (merged catalog)       ~L996            ║
    ║       §2.6 /api/feature-flags                     ~L1112           ║
    ║       §2.7 /api/smart-routing                     ~L1140           ║
    ║       §2.8 /api/ai-mode                           ~L1178           ║
    ║       §2.9 /api/path-denylist                     ~L1230           ║
    ╚════════════════════════════════════════════════════════════════════╝

    ``custom_models`` is the canonical in-memory view of the
    persisted ``custom_models.json`` — callers outside this module
    (app.py's /api/llm-models merge) read it directly. Mutations
    happen only through the PUT/DELETE handlers so the on-disk file
    stays authoritative.
    """

    router: Any
    custom_models: dict[str, dict[str, Any]]
    codex_accounts: CodexAccountService
    codex_preferences: CodexModelPreferenceStore
    codex_updates: CodexUpstreamUpdateService
    model_provider_plugins: Any


# ═══════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════


def create_config_router(
    *,
    stack: Any = None,
    custom_models_path: Path | str | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    codex_account_service: CodexAccountService | None = None,
    codex_preference_store: CodexModelPreferenceStore | None = None,
    codex_update_service: CodexUpstreamUpdateService | None = None,
    codex_state_root: Path | str | None = None,
    codex_preferences_path: Path | str | None = None,
    codex_legacy_source_home: Path | str | None = None,
    deployment_mode: str | None = None,
    credential_store: Any = None,
) -> ConfigRouter:
    """Build the FastAPI router + state bundle.

    Parameters
    ----------
    stack :
        Execution stack exposing ``.planner.router`` (a
        ``ModelDispatchRouter``). When absent, custom-model
        register/unregister become no-ops (still persisted to disk
        so a later start can register them once the stack is live).
    custom_models_path :
        Where to persist user-added models. Default preserves the
        pre-split location. Tests override with a tmp_path.
    """
    require_fastapi(__name__)

    def _auth_dep(request: Request) -> None:
        # Router-level gate keeps the whole config surface aligned with
        # the rest of the auth-aware control plane. require_auth=False
        # stays a no-op for single-user dev; auth-on deployments get a
        # consistent 401 before any config state is exposed or mutated.
        from runtime.adapters.web_auth import _resolve_actor

        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _resolve_identity(request: Request) -> Any:
        """Resolve the full Identity (with roles) for admin checks. ``None``
        when auth is disabled or no valid bearer is present."""
        if not require_auth or identity_store is None:
            return None
        auth = request.headers.get("Authorization") or ""
        if not auth.lower().startswith("bearer "):
            return None
        token = auth[7:].strip()
        if jwt_secret and token.count(".") == 2:
            try:
                identity = identity_store.verify_jwt(
                    token,
                    secret=jwt_secret,
                    required_issuer=jwt_issuer,
                    required_audience=jwt_audience,
                )
                if identity is not None:
                    return identity
            except Exception:  # noqa: BLE001 — fall through to api-key check
                pass
        try:
            return identity_store.verify_api_key(token)
        except Exception:  # noqa: BLE001 — unresolved identity → no roles
            return None

    def _require_admin(request: Request) -> None:
        """Mutating config endpoints that are themselves security controls (the
        path-denylist is one) need the ``admin`` role when auth is on. The
        router-level ``_auth_dep`` already enforced authentication; this adds
        the role gate on top. Dev mode (require_auth=False) stays a no-op for
        single-user local use."""
        if not require_auth:
            return
        identity = _resolve_identity(request)
        roles = getattr(identity, "roles", ()) or ()
        if "admin" not in {str(r).lower() for r in roles}:
            raise HTTPException(403, "admin role required")

    path = (
        Path(custom_models_path)
        if custom_models_path is not None
        else app_paths().custom_models_path
    )
    custom_models_state: dict[str, dict[str, Any]] = {}
    # FastAPI executes these synchronous handlers in a shared thread pool.
    # Serialize custom-model reads and mutations so concurrent PUT/DELETE/import
    # requests cannot race the read-modify-write persistence cycle or expose a
    # partially rebuilt dispatch table to listing endpoints.
    custom_models_lock = threading.RLock()

    resolved_codex_root = resolve_codex_state_root(codex_state_root)
    resolved_deployment = (
        str(deployment_mode or os.environ.get("ECHO_DEPLOYMENT_MODE") or "local").strip().casefold()
    )
    legacy_codex_home: Path | str | None = None
    if resolved_deployment == "local":
        legacy_codex_home = (
            codex_legacy_source_home
            if codex_legacy_source_home is not None
            else os.environ.get("ECHO_CODEX_SOURCE_HOME") or Path.home() / ".codex"
        )
    codex_accounts = codex_account_service or CodexAccountService(
        resolved_codex_root,
        legacy_source_home=legacy_codex_home,
        # Local desktop is one OS-user trust boundary even when Echo login
        # supplies a non-null principal. Shared/server modes keep strict
        # principal-scoped ChatGPT credentials and never inherit host auth.
        allow_local_principal_inheritance=resolved_deployment == "local",
    )
    codex_preferences = codex_preference_store or CodexModelPreferenceStore(
        Path(codex_preferences_path).expanduser().resolve(strict=False)
        if codex_preferences_path is not None
        else resolved_codex_root / "model_profile.json"
    )
    codex_updates = codex_update_service or CodexUpstreamUpdateService(
        resolved_codex_root / "upstream_update.json",
        check_interval_seconds=float(
            os.environ.get("ECHO_CODEX_UPDATE_CHECK_INTERVAL_SECONDS") or 6 * 60 * 60
        ),
        initial_check_delay_seconds=float(
            os.environ.get("ECHO_CODEX_UPDATE_INITIAL_DELAY_SECONDS") or 15
        ),
    )

    @asynccontextmanager
    async def _config_lifespan(_app: Any):
        codex_accounts.start_idle_reaper()
        codex_updates.start()
        try:
            yield
        finally:
            await codex_updates.close()
            await codex_accounts.close_all()

    router = APIRouter(
        tags=["config"],
        dependencies=[Depends(_auth_dep)],
        lifespan=_config_lifespan,
    )

    def _serialize_custom_models(handler: Any) -> Any:
        @wraps(handler)
        def _locked(*args: Any, **kwargs: Any) -> Any:
            with custom_models_lock:
                return handler(*args, **kwargs)

        return _locked

    def _custom_models_snapshot() -> dict[str, dict[str, Any]]:
        with custom_models_lock:
            return {model_id: dict(entry) for model_id, entry in custom_models_state.items()}

    # ─── Persistence helpers ────────────────────────────────
    # These used to be nested inside create_app; moving them here
    # keeps the "own your state" principle — same file, one level of
    # scoping instead of two.

    def _disk_state() -> dict[str, dict[str, Any]]:
        """Entries currently on disk, or an empty mapping if unreadable."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict)}

    def _save(*touched: str) -> None:
        """Persist, writing through only the ids this request changed.

        This file is routinely hand-edited — it is where an operator sets
        base urls and api keys — while our in-memory copy is refreshed
        only at startup. Writing that snapshot wholesale therefore
        silently reverted every edit made since boot, and could bring
        back an entry the operator had removed.

        So disk is the source of truth and each caller names the ids it
        actually mutated: those are applied (or removed, when the caller
        dropped them from memory) and nothing else is touched. Passing no
        ids makes this a no-op write, which keeps a bare ``save()`` from
        clobbering anything.
        """
        with custom_models_lock:
            merged = _disk_state()
            for model_id in touched:
                entry = custom_models_state.get(model_id)
                if entry is None:
                    merged.pop(model_id, None)
                else:
                    merged[model_id] = entry
            temp_path: Path | None = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                # Same-directory replace keeps readers from observing a
                # partially-written JSON document after a crash or concurrent
                # request. NamedTemporaryFile is closed before os.replace for
                # Windows compatibility.
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=path.parent,
                    prefix=f".{path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temp_path = Path(handle.name)
                    json.dump(merged, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, path)
                temp_path = None
            except OSError as exc:  # noqa: BLE001 — in-memory mutation already succeeded
                # Preserve the historical best-effort API contract, but make
                # restart data-loss risk visible to operators.
                logging.getLogger(__name__).error(
                    "failed to persist custom model config to %s: %s",
                    path,
                    exc,
                )
            finally:
                if temp_path is not None:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except OSError:
                        logging.getLogger(__name__).debug(
                            "failed to remove temporary custom model config %s",
                            temp_path,
                            exc_info=True,
                        )

    def _load() -> None:
        with custom_models_lock:
            try:
                raw = path.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    for entry in data.values():
                        if isinstance(entry, dict):
                            entry.pop("max_tokens", None)
                    custom_models_state.update(
                        {k: v for k, v in data.items() if isinstance(v, dict)}
                    )
                    # Boot-time rewrite: the loop above stripped the retired
                    # ``max_tokens`` field, so every id we just read is one
                    # we changed and has to be written back.
                    _save(*custom_models_state)
            except (OSError, json.JSONDecodeError):  # noqa: BLE001 — fresh install or corrupt file; start empty rather than crash boot
                # Fresh install (no file) or corrupted file — start empty
                # rather than crashing app boot. Corrupt files should be
                # inspected by ops, not auto-wiped.
                pass

    # ─── Dispatcher registration · sub-router builder ─────────
    # Given a user-supplied provider config, construct the right
    # ``ModelRouter`` subclass and register it under the user's
    # chosen model_id on the live dispatcher. Wrapped so the request
    # carrying our dispatch alias ("claude-mirror") gets the
    # upstream real model name ("claude-sonnet-4-6") substituted
    # before the sub-router sees it — without that wrap mirrors
    # reject the alias as an unknown model.

    def _dispatcher() -> Any:
        return getattr(
            getattr(stack, "planner", None) if stack else None,
            "router",
            None,
        )

    def _unregister_entry(
        entry: dict[str, Any] | None,
        *,
        fallback_id: str = "",
    ) -> bool:
        dispatcher = _dispatcher()
        if dispatcher is None or not hasattr(dispatcher, "unregister"):
            return False
        route_ids = (
            _entry_route_ids(entry, fallback_id)
            if isinstance(entry, dict)
            else ([fallback_id] if fallback_id else [])
        )
        removed = False
        for route_id in route_ids:
            removed = bool(dispatcher.unregister(route_id)) or removed
        return removed

    def _register(entry: dict[str, Any]) -> dict[str, Any]:
        dispatcher = _dispatcher()
        if dispatcher is None or not hasattr(dispatcher, "register"):
            return {"ok": False, "error": "planner has no ModelDispatchRouter"}
        model_id = _entry_model_id(entry)
        if not model_id:
            return {"ok": False, "error": "id required"}
        provider = (entry.get("provider") or "openai").lower()
        base_url = entry.get("base_url") or ""
        api_key = entry.get("api_key") or ""
        if not api_key and entry.get("credential_ref"):
            from runtime.platform.models.model_provider_plugin import (
                resolve_model_provider_api_key,
            )

            api_key = resolve_model_provider_api_key(
                entry,
                credential_store=credential_store,
            )
        upstreams = _entry_upstreams(entry, model_id)
        if not upstreams:
            return {"ok": False, "error": "models list is empty"}
        primary_model = upstreams[0]
        responses_models = {
            str(model).strip()
            for model in (entry.get("responses_models") or [])
            if str(model or "").strip() in upstreams
        }
        responses_router: Any | None = None
        default_headers = entry.get("default_headers") or {}
        if not isinstance(default_headers, dict):
            default_headers = {}
        try:
            if provider in ("anthropic", "claude"):
                from runtime.sensing.model_router.anthropic_router import (
                    AnthropicModelRouter,
                )

                sub_router: Any = AnthropicModelRouter(
                    api_key=api_key,
                    default_model=primary_model,
                    base_url=(base_url or None),
                )
            elif provider in ("gemini", "google"):
                from runtime.sensing.model_router.gemini_router import (
                    GeminiModelRouter,
                )

                sub_router = GeminiModelRouter(
                    api_key=api_key,
                    default_model=primary_model,
                    base_url=(base_url or "https://generativelanguage.googleapis.com/v1beta"),
                    extra_headers=default_headers,
                )
            else:
                from runtime.sensing.model_router.openai_router import (
                    OpenAIModelRouter,
                )

                if not base_url:
                    return {
                        "ok": False,
                        "error": "base_url required for openai-compat",
                    }
                sub_router = OpenAIModelRouter(
                    base_url=base_url,
                    api_key=api_key or "dummy",
                    default_model=primary_model,
                    extra_headers=default_headers,
                    custom_model_entry=entry,
                )
                if responses_models:
                    from runtime.sensing.model_router.openai_responses_router import (
                        OpenAIResponsesModelRouter,
                    )

                    responses_router = OpenAIResponsesModelRouter(
                        base_url=base_url,
                        api_key=api_key or "dummy",
                        default_model=next(iter(responses_models)),
                        extra_headers=default_headers,
                        provider_name=str(entry.get("compat_profile") or model_id),
                    )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"router init failed: {e}"}

        from runtime.sensing.model_router.models import (
            ModelRequest as _MR,  # noqa: N814
        )
        from runtime.sensing.model_router.models import (
            ModelRouter as _MRR,  # noqa: N814
        )

        class _UpstreamModelRewrite(_MRR):
            """Dispatch wrapper that maps a request to one of the
            entry's ``upstreams`` slots, then delegates to the inner
            provider-specific router.

            Behavior:
              * If ``request.model`` is one of the entry's
                ``upstreams``, use it as the real upstream id. This
                is the path Auto mode takes after
                ``turn_complexity`` picks ``entry.models[0]`` (cheap
                tier) or ``entry.models[-1]`` (performance tier) and
                rewrites ``request.model`` to that concrete value.
              * Otherwise fall back to ``upstreams[0]``. This covers
                the user-pinned path in ModelPicker — when the user
                picks the entry id (e.g. ``openai-prod``) rather than
                a specific model name, we still want the cheap slot
                by default and let the user re-pick via Auto mode for
                a stronger tier.
            """

            def __init__(
                self,
                inner: _MRR,
                upstreams: list[str],
                selection_models: dict[str, str],
                responses_inner: _MRR | None = None,
                responses_models: set[str] | None = None,
            ) -> None:
                self._inner = inner
                self._responses_inner = responses_inner
                self._responses_models = set(responses_models or ())
                self._upstreams = list(upstreams)
                self._selection_models = dict(selection_models)
                self._default = self._upstreams[0] if self._upstreams else ""

            def _resolve(self, request: _MR) -> str:
                selected = self._selection_models.get(request.model)
                if selected is not None:
                    return selected
                requested = request.model.removesuffix("::1m")
                if requested in self._upstreams:
                    return requested
                return self._default

            def call(self, request: _MR):
                resolved = self._resolve(request)
                rewritten = request.model_copy(update={"model": resolved})
                inner = (
                    self._responses_inner
                    if self._responses_inner is not None and resolved in self._responses_models
                    else self._inner
                )
                return inner.call(rewritten)

            def call_stream(self, request: _MR):
                # Mirror ``call`` · route to the right upstream slot,
                # then delegate to the inner router's streaming path.
                # Required for multi-model entries (e.g. ``openai-prod``
                # with ``[gpt-4o-mini, gpt-4o]``) to emit real deltas
                # from the chosen slot instead of always-defaulting
                # to the cheap one.
                resolved = self._resolve(request)
                rewritten = request.model_copy(update={"model": resolved})
                inner = (
                    self._responses_inner
                    if self._responses_inner is not None and resolved in self._responses_models
                    else self._inner
                )
                yield from inner.call_stream(rewritten)

            @property
            def default_model(self) -> str:
                return self._default

        selection_models = {
            selection.selection_id: selection.model
            for selection in selections_for_entry(model_id, entry)
        }
        wrapper = _UpstreamModelRewrite(
            sub_router,
            upstreams,
            selection_models,
            responses_inner=responses_router,
            responses_models=responses_models,
        )
        for route_id in _entry_route_ids(entry, model_id):
            dispatcher.register(route_id, wrapper)
        return {"ok": True, "model_id": model_id}

    def _unregister(model_id: str) -> bool:
        return _unregister_entry(
            custom_models_state.get(model_id),
            fallback_id=model_id,
        )

    def _rebuild_routes() -> dict[str, dict[str, Any]]:
        """Re-register all live entries in stable insertion order.

        Legacy model aliases can intentionally overlap across entries. Removing
        or updating the entry that currently owns such an alias first removes
        its old routes; replaying the remaining entries restores the alias to
        the last still-configured owner while row-level selection ids stay
        unambiguous.
        """
        return {model_id: _register(entry) for model_id, entry in custom_models_state.items()}

    from runtime.platform.models.model_provider_plugin import (
        ModelProviderPluginManager,
    )

    model_provider_plugins = ModelProviderPluginManager(
        custom_models=custom_models_state,
        lock=custom_models_lock,
        save=_save,
        unregister_entry=_unregister_entry,
        rebuild_routes=_rebuild_routes,
        credential_store=credential_store,
    )

    # Hydrate from disk + re-register each entry so the dispatcher
    # sees them on the first request.
    _load()
    for _entry in custom_models_state.values():
        _register(_entry)

    _build_endpoints(
        _ConfigCtx(
            router=router,
            custom_models=custom_models_state,
            custom_models_snapshot=_custom_models_snapshot,
            save=_save,
            load=_load,
            register=_register,
            unregister_entry=_unregister_entry,
            rebuild_routes=_rebuild_routes,
            serialize_custom_models=_serialize_custom_models,
            require_admin=_require_admin,
            stack=stack,
            codex_accounts=codex_accounts,
            codex_preferences=codex_preferences,
            codex_updates=codex_updates,
        )
    )

    return ConfigRouter(
        router=router,
        custom_models=custom_models_state,
        codex_accounts=codex_accounts,
        codex_preferences=codex_preferences,
        codex_updates=codex_updates,
        model_provider_plugins=model_provider_plugins,
    )


__all__ = [
    "ConstitutionProfilePutBody",
    "ConstitutionProfileResponse",
    "ConfigRouter",
    "CustomModelDeleteResponse",
    "CustomModelEntry",
    "CustomModelsList",
    "CustomModelTestResponse",
    "CustomModelUpsertResponse",
    "CustomModelUpsertStatus",
    "IdentityLockPutBody",
    "IdentityLockResponse",
    "ProviderCapabilitiesWire",
    "ProvidersResponse",
    "create_config_router",
]
