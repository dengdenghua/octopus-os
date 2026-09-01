from __future__ import annotations

import logging
import threading
from typing import Any

from .models import ModelRequest, ModelResponse, ModelRouter
from .rescue_policy import (
    is_retryable_model_error as _is_provider_unavailable_error,
)
from .rescue_policy import (
    model_rescue_quality as _model_rescue_quality,
)
from .vision_guard import (
    apply_vision_guard,
    build_without_images,
    classify_image_rejection,
    request_has_images,
)

_LOG = logging.getLogger("runtime.sensing.model_router.dispatch")


class ModelDispatchRouter(ModelRouter):
    def __init__(
        self,
        *,
        fallback: ModelRouter,
        routes: dict[str, ModelRouter] | None = None,
    ) -> None:
        self._fallback = fallback
        self._routes: dict[str, ModelRouter] = dict(routes or {})
        self._lock = threading.RLock()

    # ─── public registry API ──────────────────────────────────

    def register(self, model_id: str, router: ModelRouter) -> None:
        with self._lock:
            self._routes[model_id] = router

    def unregister(self, model_id: str) -> bool:
        with self._lock:
            return self._routes.pop(model_id, None) is not None

    def list_routes(self) -> list[str]:
        with self._lock:
            return sorted(self._routes.keys())

    def has(self, model_id: str) -> bool:
        with self._lock:
            return model_id in self._routes

    def set_fallback(self, router: ModelRouter) -> None:
        self._fallback = router

    # ─── ModelRouter interface ────────────────────────────────

    def call_stream(self, request: ModelRequest):
        picked = self._resolve(request.model)
        # Vision pre-guard: a model the operator marked non-vision never
        # sees a raw image — images are transcribed (or stripped) before
        # the upstream call, so a picture can't crash the turn up front.
        guarded = apply_vision_guard(self._rewrite_unrouted(request))
        yielded_any = False
        try:
            for evt in picked.call_stream(guarded):
                yielded_any = True
                yield evt
        except Exception as exc:
            # Vision crash recovery for streams · only when nothing has
            # been yielded yet: a partial stream already showed the user
            # content, and replaying it on a stripped request would
            # duplicate the reply. The pre-guard gate (``request_has_
            # images(guarded)``) also keeps recovery off when a known
            # non-vision model already got its images stripped.
            if not yielded_any and request_has_images(guarded) and classify_image_rejection(exc):
                try:
                    for evt in picked.call_stream(build_without_images(guarded)):
                        yield evt
                    return
                except Exception:
                    # The stripped retry also failed → the failure is not
                    # the image; re-raise the original so we never mask.
                    raise exc from None
            if not yielded_any and _is_provider_unavailable_error(exc):
                # A row-level selection id is an explicit endpoint choice from
                # the model picker.  Silently rescuing it through another
                # provider both violates that choice and replaces the useful
                # upstream error with a misleading provider-branded one.
                rescue = (
                    None
                    if request.model.startswith("echo-custom-model:v1:")
                    else self._pick_provider_rescue(request.model, picked)
                )
                if rescue is not None:
                    rescue_model, rescue_router = rescue
                    rewritten = request.model_copy(update={"model": rescue_model})
                    # Pre-guard the rescue payload so a rescue model that
                    # is KNOWN non-vision never sees the original images.
                    # A residual image rejection on an unknown rescue
                    # model surfaces like any other rescue-stream error —
                    # the generator is lazy, so it cannot be caught here.
                    yield from _stamp_stream_model(
                        rescue_router.call_stream(
                            apply_vision_guard(rewritten),
                        ),
                        rescue_model,
                    )
                    return
            if yielded_any or picked is not self._fallback:
                raise
            exc_class = type(exc).__name__
            exc_msg = str(exc)
            auth_shaped = (
                "Credentials" in exc_class
                or "Unauthorized" in exc_class
                or "Auth" in exc_class
                or "current_actor" in exc_msg
                or "登录态" in exc_msg
            )
            if not auth_shaped:
                raise
            rescue = self._pick_guest_rescue()
            if rescue is None:
                raise
            try:
                rescue_default = (
                    getattr(
                        rescue,
                        "default_model",
                        None,
                    )
                    or request.model
                )
                rewritten = request.model_copy(
                    update={"model": rescue_default},
                )
                yield from rescue.call_stream(
                    apply_vision_guard(rewritten),
                )
            except (ConnectionError, TimeoutError, TypeError, ValueError):
                raise exc from None

    def call(self, request: ModelRequest) -> ModelResponse:
        picked = self._resolve(request.model)
        # Vision pre-guard: a model the operator marked non-vision never
        # sees a raw image (transcribe/strip before the upstream call).
        guarded = apply_vision_guard(self._rewrite_unrouted(request))
        try:
            return picked.call(guarded)
        except Exception as exc:
            # Vision crash recovery: a model that rejected the image
            # payload (declared-vision or undeclared) gets one retry
            # with images transcribed/stripped, so the turn continues
            # instead of dying. Runs before provider-rescue — a 4xx
            # image rejection is disjoint from the retryable markers,
            # so the two never collide. If the stripped retry ALSO
            # fails we re-raise the ORIGINAL error (never mask). The
            # ``request_has_images(guarded)`` gate keeps recovery off
            # when a known non-vision model already got its images
            # stripped (its 4xx is about something else).
            if request_has_images(guarded) and classify_image_rejection(exc):
                try:
                    return picked.call(build_without_images(guarded))
                except Exception:
                    raise exc from None
            if _is_provider_unavailable_error(exc):
                rescue = (
                    None
                    if request.model.startswith("echo-custom-model:v1:")
                    else self._pick_provider_rescue(request.model, picked)
                )
                if rescue is not None:
                    rescue_model, rescue_router = rescue
                    rewritten = request.model_copy(update={"model": rescue_model})
                    response = _call_with_vision_recovery(
                        rescue_router,
                        apply_vision_guard(rewritten),
                        original_exc=exc,
                    )
                    return response.model_copy(update={"model": rescue_model})
            # Guest-mode rescue · an account-backed fallback may require
            # a logged-in actor. A guest with ONE registered custom
            # model of the same family should just use that. Without
            # this rescue every guest turn that doesn't explicitly
            # set model_name crashes with "no current_actor set".
            #
            # Trigger conditions:
            #   * ``picked`` is the fallback (not a named sub-router)
            #   * the exception looks like an auth/credentials issue
            #   * a rescue candidate is registered
            #
            # We check by class NAME rather than importing provider-specific
            # exceptions, which keeps this dispatcher provider-neutral.
            if picked is self._fallback:
                exc_class = type(exc).__name__
                exc_msg = str(exc)
                auth_shaped = (
                    "Credentials" in exc_class
                    or "Unauthorized" in exc_class
                    or "Auth" in exc_class
                    or "current_actor" in exc_msg
                    or "登录态" in exc_msg
                )
                if auth_shaped:
                    rescue = self._pick_guest_rescue()
                    if rescue is not None:
                        # Rewrite request.model so the rescue router
                        # (usually an anthropic sub-router) accepts it.
                        try:
                            rescue_default = (
                                getattr(
                                    rescue,
                                    "default_model",
                                    None,
                                )
                                or request.model
                            )
                            rewritten = request.model_copy(
                                update={"model": rescue_default},
                            )
                            return rescue.call(
                                apply_vision_guard(rewritten),
                            )
                        except (ConnectionError, TimeoutError, TypeError, ValueError):  # noqa: BLE001 — rescue router failed; re-raise original error
                            pass
            raise

    def _pick_provider_rescue(
        self,
        model_id: str,
        failed_router: ModelRouter,
    ) -> tuple[str, ModelRouter] | None:
        """Pick the strongest distinct route after an unavailable model.

        A code-specialist outage must not silently downgrade a repair task to
        the first cheap/chat entry in catalog order.  Rank model ids by their
        advertised capability, while preserving registration order between
        equal-quality candidates.  Aliases that point at the same router are
        still deduplicated so an entry registered under both its id and
        concrete model cannot retry itself.
        """
        with self._lock:
            routes = list(self._routes.items())
        if not routes:
            return None
        indexed = list(enumerate(routes))
        ordered = [
            route
            for _idx, route in sorted(
                indexed,
                key=lambda row: (-_model_rescue_quality(row[1][0]), row[0]),
            )
        ]
        seen = {id(failed_router)}
        for name, router in ordered:
            if id(router) in seen:
                continue
            seen.add(id(router))
            return name, router
        return None

    def _pick_guest_rescue(self) -> ModelRouter | None:
        """Pick a registered sub-router suitable as a guest-mode rescue.

        Heuristic: any registered router other than the fallback itself.
        When multiple are registered we prefer anthropic-family by
        inspecting ``provider_name`` on the sub-router or its wrapped
        inner (handles the ``_UpstreamModelRewrite`` wrapper that
        config_router.py uses to bind custom-model aliases). Returns
        None when no rescue is available · caller then re-raises the
        original auth error.
        """
        with self._lock:
            candidates = list(self._routes.values())
        if not candidates:
            return None

        def _provider(r: ModelRouter) -> str:
            # Unwrap one layer · custom-models register the real router
            # inside ``_UpstreamModelRewrite(inner=...)`` and the
            # provider_name lives on inner.
            inner = getattr(r, "_inner", r)
            return str(getattr(inner, "provider_name", "") or "").lower()

        # Stable preference order · anthropic first (most users have
        # claude-mirror configured), then openai-compat, then anything.
        priority = {"anthropic": 0, "openai": 1, "gemini": 2}
        candidates.sort(key=lambda r: priority.get(_provider(r), 99))
        return candidates[0]

    def _route_for(self, model_id: str) -> ModelRouter | None:
        """Return the router explicitly registered for ``model_id``.

        Checks the exact id first, then the ``provider/...`` prefix form
        (``"openai/gpt-4o"`` → the router registered under ``"openai"``).
        ``None`` means the model has no dedicated route and would fall
        through to the fallback router.
        """
        with self._lock:
            r = self._routes.get(model_id)
            if r is not None:
                return r
            prefix = model_id.split("/", 1)[0] if "/" in model_id else None
            if prefix:
                r = self._routes.get(prefix)
            return r

    def _resolve(self, model_id: str) -> ModelRouter:
        return self._route_for(model_id) or self._fallback

    def _rewrite_unrouted(self, request: ModelRequest) -> ModelRequest:
        """Gracefully degrade an unrouted model to the fallback's default.

        A model name with no registered route falls through to the fallback
        router.  If that router's provider only serves its own model ids
        (e.g. a deepseek-compatible endpoint), forwarding the literal name
        (say ``claude-haiku-4-5``) upstream is an opaque ``400`` that kills
        the whole turn.  Rewrite to the fallback's default model instead and
        log it, so an unconfigured model request still runs rather than
        hard-failing.  Explicitly routed models are never rewritten.
        """
        if self._route_for(request.model) is not None:
            return request
        if self._resolve(request.model) is not self._fallback:
            return request
        default = getattr(self._fallback, "default_model", None)
        if not default or default == request.model:
            return request
        _LOG.warning(
            "model '%s' is not routed by any provider; falling back to default '%s'",
            request.model,
            default,
        )
        return request.model_copy(update={"model": default})

    # Expose default_model so upstream wrappers (e.g. MultiModelRouter) that
    # rewrite request.model won't clobber user-supplied ids.
    @property
    def default_model(self) -> Any:
        return getattr(self._fallback, "default_model", None)


def _stamp_stream_model(events: Any, model_id: str):
    """Make a transparent rescue visible to downstream sticky routing."""
    for event in events:
        if event.type == "done" and event.final is not None:
            final = event.final.model_copy(update={"model": model_id})
            event = event.model_copy(update={"final": final})
        yield event


def _call_with_vision_recovery(
    router: ModelRouter,
    request: ModelRequest,
    *,
    original_exc: BaseException,
) -> ModelResponse:
    """Call ``router``, recovering one image rejection with a strip.

    ``request`` is already pre-guarded; this covers the residual case
    where the router's model is not KNOWN non-vision but still rejects
    the image payload (e.g. an undeclared rescue model). If the stripped
    retry also fails, the caller's original error is re-raised so a
    retryable failure is never masked by a secondary one.
    """
    try:
        return router.call(request)
    except Exception as exc:
        if request_has_images(request) and classify_image_rejection(exc):
            try:
                return router.call(build_without_images(request))
            except Exception:
                raise original_exc from None
        raise
