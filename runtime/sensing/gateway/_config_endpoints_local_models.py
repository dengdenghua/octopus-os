"""Local-model discovery + one-click import endpoints for the config router.

Pure structural split of ``_config_endpoints.py`` — no logic changes.
``_register_local_models`` attaches the ``/scan`` and ``/import`` endpoints
that back the "本地模型" collapsible in the settings page, reading shared
state through the injected ``_ConfigCtx``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import Depends

from runtime.sensing.gateway._config_helpers import _custom_model_wire_entry

if TYPE_CHECKING:
    from ._config_endpoints import _ConfigCtx


def _register_local_models(router: Any, ctx: _ConfigCtx) -> None:
    custom_models_state = ctx.custom_models
    save = ctx.save
    unregister_entry = ctx.unregister_entry
    rebuild_routes = ctx.rebuild_routes
    require_admin = ctx.require_admin

    # ─── Local-model discovery + one-click import ────────────
    # These two endpoints back the "本地模型" collapsible in the
    # settings page · operators with Ollama / LM Studio / vLLM /
    # llama.cpp already running shouldn't have to hand-fill the
    # base_url for each. ``scan`` probes a small set of well-known
    # ports in parallel and reports what's reachable; ``import``
    # then takes one of those entries and writes it into
    # ``custom_models_state`` via the same code path that
    # ``api_upsert_custom_model`` uses, so the dispatcher and
    # smart-routing tier resolution pick it up immediately.
    #
    # We deliberately *do not* auto-import — operators should see
    # what's on their box first (avoids leaking dev-only services
    # into the production dispatch table), but the import itself
    # is one click and pre-fills models/base_url.

    @router.get(
        "/api/config/local-models/scan",
        dependencies=[Depends(require_admin)],
    )
    def api_scan_local_models(targets: str | None = None) -> dict[str, Any]:
        """Probe common local LLM server ports in parallel. Each
        probe tries the OpenAI-compat ``/v1/models`` endpoint; for
        Ollama we also try ``/api/tags`` as a fallback because
        Ollama only serves the OpenAI-compat surface when its
        ``OLLAMA_ORIGINS`` setting is permissive.

        ``targets`` is an optional comma-separated list of base
        URLs to probe instead of the production defaults. Tests
        use it to point the scanner at a temporary ``http.server``
        without monkeypatching private state; operators can use
        it to probe a remote dev box (e.g.
        ``?targets=http://10.0.0.5:11434,http://10.0.0.5:1234``).

        Returned shape:
          ``{"services": [ { provider, base_url, models, status,
          error? }, ... ]}``

        ``status`` is one of:
          * ``"ok"`` — service responded with at least one model id
          * ``"empty"`` — service responded, no models listed (yet)
          * ``"error"`` — service responded but with an unexpected
            shape, or the connection failed (see ``error``)

        Services that didn't respond at all are simply omitted from
        the list — no need to surface a "port 8001 is dead" row
        for every silent port on the box.
        """
        import concurrent.futures

        # Each candidate is (provider_hint, base_url, model_path).
        # ``provider_hint`` is what we'd tell the dispatcher if the
        # user imports this row — Ollama is OpenAI-compat at ``/v1``
        # when that surface is enabled, otherwise it still serves
        # the native ``/api/tags`` schema which our probe path
        # handles separately. Other rows are stock OpenAI-compat.
        if targets:
            override: list[tuple[str, str, str]] = []
            for raw in targets.split(","):
                base = raw.strip().rstrip("/")
                if not base:
                    continue
                override.append(("openai", base, "/v1/models"))
            candidates = override
        else:
            candidates = [
                ("openai", "http://127.0.0.1:11434", "/v1/models"),
                ("openai", "http://127.0.0.1:1234", "/v1/models"),
                ("openai", "http://127.0.0.1:8000", "/v1/models"),
                ("openai", "http://127.0.0.1:8001", "/v1/models"),
                ("openai", "http://127.0.0.1:8080", "/v1/models"),
            ]
        # Ollama native probe — separate candidate so the error
        # path is "Ollama isn't running" rather than "the v1 surface
        # isn't enabled". Only meaningful for the 11434 row.
        ollama_native = ("openai", "http://127.0.0.1:11434", "/api/tags")

        def _probe(
            base: str,
            path: str,
            timeout: float = 0.6,
        ) -> tuple[list[str], str | None]:
            url = base.rstrip("/") + path
            try:
                from runtime.safety.auth.url_guard import safe_httpx_request

                # Local-model discovery intentionally permits loopback/RFC1918
                # targets, but still constrains schemes, pins DNS, refuses
                # redirects, and caps the body before buffering it.
                resp = safe_httpx_request(
                    "GET",
                    url,
                    headers={"Accept": "application/json"},
                    timeout=timeout,
                    allow_private=True,
                    follow_redirects=False,
                    read_cap_bytes=256_000,
                )
                if not (200 <= resp.status_code < 300):
                    return [], f"http {resp.status_code}"
                payload = resp.content.decode("utf-8", errors="replace")
            except Exception as e:  # noqa: BLE001 - each probe is best-effort
                return [], f"connection: {type(e).__name__}"
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                return [], "invalid json"
            # OpenAI-compat shape · ``data[].id``
            if isinstance(data, dict) and isinstance(data.get("data"), list):
                ids = [
                    model_id
                    for m in data["data"]
                    if isinstance(m, dict) and isinstance(model_id := m.get("id"), str)
                ]
                return ids, None
            # Ollama native shape · ``models[].name``
            if isinstance(data, dict) and isinstance(data.get("models"), list):
                names = [
                    model_name
                    for m in data["models"]
                    if isinstance(m, dict) and isinstance(model_name := m.get("name"), str)
                ]
                return names, None
            return [], "unexpected schema"

        # Run all probes concurrently so a full scan completes in
        # ~one timeout window even when several ports are dead.
        results: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futures = {
                ex.submit(_probe, base, path): (base, path) for (provider, base, path) in candidates
            }
            for fut in concurrent.futures.as_completed(futures):
                base, path = futures[fut]
                ids, err = fut.result()
                if err is not None and "connection" in err:
                    # Silent port — drop it, the UI doesn't need
                    # to see every dead localhost listener.
                    continue
                # Decide provider hint from the probed base. The
                # base URL is what the UI will pre-fill; we don't
                # need to distinguish "ollama native" vs "ollama
                # v1" because both end up routed through the
                # OpenAI-compat adapter with the same base_url.
                provider_hint = "openai"
                base_url = base.rstrip("/") + "/v1"  # normalize
                results.append(
                    {
                        "provider": provider_hint,
                        "base_url": base_url,
                        "probe_path": path,
                        "models": ids,
                        "status": "ok" if ids else "empty",
                        **({"error": err} if err else {}),
                    }
                )
        # Ollama native fallback — only when nothing on 11434's
        # v1 surface came back. Avoids double-reporting.
        if not any(r["base_url"].startswith("http://127.0.0.1:11434") for r in results):
            # Tuple is (provider_hint, base_url, probe_path); we
            # want the base_url and probe_path here.
            _, ollama_base, ollama_path = ollama_native
            ids, err = _probe(ollama_base, ollama_path)
            if err is None:
                results.append(
                    {
                        "provider": "openai",
                        "base_url": "http://127.0.0.1:11434/v1",
                        "probe_path": ollama_path,
                        "models": ids,
                        "status": "ok" if ids else "empty",
                    }
                )
        return {"services": results}

    @router.post(
        "/api/config/local-models/import",
        dependencies=[Depends(require_admin)],
    )
    @ctx.serialize_custom_models
    def api_import_local_model(body: dict[str, Any]) -> dict[str, Any]:
        """Take one row from ``/scan`` and write it into
        ``custom_models_state``. Mirrors what
        ``api_upsert_custom_model`` does for the public PUT path
        but skips the HTTP round-trip — local imports are trusted.

        Body shape:
          ``{ base_url, models, display_name?, id? }``

        ``id`` defaults to the first non-empty model id (the
        picker default) so the dispatcher's lookup key matches
        the user's mental model. ``api_key`` is left empty —
        local services don't need one, and the OpenAI-compat
        router ships a dummy key for unauthenticated calls.
        """
        base_url = str(body.get("base_url") or "").rstrip("/")
        if not base_url:
            return {"ok": False, "error": "base_url required"}
        raw_models = body.get("models")
        if not isinstance(raw_models, list):
            return {"ok": False, "error": "models list required"}
        models = [str(m).strip() for m in raw_models if str(m or "").strip()]
        if not models:
            return {"ok": False, "error": "at least one model id required"}
        display_name = str(body.get("display_name") or models[0])
        # Stable id derived from the base URL host:port (so two
        # imports of the same service overwrite each other instead
        # of stacking duplicate rows). The user can still rename
        # in the edit form afterwards.
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        # sanitize for filesystem/id safety
        id_seed = f"local-{host}-{port}".replace(".", "-")
        provided_id = body.get("id")
        if isinstance(provided_id, str) and provided_id.strip():
            id_seed = provided_id.strip()
        model_id = id_seed
        # Avoid clobbering an unrelated entry whose id collides —
        # append a numeric suffix until we hit a free slot, but
        # never overwrite an existing import of the same id (the
        # caller can re-run scan to refresh its models list and
        # we'll just update in place).
        suffix = 1
        while (
            model_id in custom_models_state
            and custom_models_state[model_id].get(
                "base_url",
            )
            != base_url
        ):
            suffix += 1
            model_id = f"{id_seed}-{suffix}"
        entry = {
            "id": model_id,
            "name": model_id,
            "provider": "openai",
            "base_url": base_url,
            "api_key": "",
            "models": models,
            "display_name": display_name,
            "supports_thinking": False,
            "supports_vision": False,
            "supports_tool_use": True,
            "omit_sampling_parameters": None,
            "compat_profile": None,
            "thinking_request_style": None,
            "drop_tool_choice": None,
            "strict_tool_schema": None,
            "max_temperature": None,
            "unsupported_request_fields": None,
            "default_headers": {},
        }
        previous = custom_models_state.get(model_id)
        if previous:
            unregister_entry(previous, fallback_id=model_id)
        custom_models_state[model_id] = entry
        save(model_id)
        status = rebuild_routes().get(
            model_id,
            {"ok": False, "error": "local model disappeared during route rebuild"},
        )
        return {
            "ok": True,
            "model_id": model_id,
            "entry": _custom_model_wire_entry(entry),
            "_status": status,
        }
