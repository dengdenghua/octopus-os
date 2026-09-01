"""
MCP router · declare / enable / disable MCP servers at runtime.

Extracted from the monolithic ``runtime/platform/ui/app.py`` in the
app.py-split campaign. Owns the Model-Context-Protocol
adapter surface: known presets (one-click ``@page-agent/mcp``
install), runtime spawn bookkeeping, and the JSON endpoints the
frontend Settings → MCP page speaks to.

Endpoints
---------

    GET  /api/mcp/config        · currently-declared servers
    PUT  /api/mcp/config        · enable/disable servers + spawn/kill

State
-----

The factory returns a wrapper carrying two mutable dicts:

    ``config_state``  — declared state (name → enabled + description
                        + command/args/env). Seeds at startup from
                        ``stack.config.mcp_servers``, mutates on PUT.
    ``runtime_state`` — live-registered spawn bookkeeping (name →
                        registered skill names + effective command).
                        Populated when a PUT flips ``enabled=true``;
                        drained when flipped off.

Exposing these lets other app wiring (health endpoint, UI app
startup log) inspect what's registered without re-doing the work.
"""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass, field
from typing import Any

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

# ═══════════════════════════════════════════════════════════
# Known-server presets · one-click install for well-known MCPs
# ═══════════════════════════════════════════════════════════
#
# Each entry gives the UI enough to spawn without the user typing
# command / args. ``bridge: "oct"`` triggers the account-backed LLM
# env flow (see ``_resolve_oct_bridge_env``).


MCP_PRESETS: dict[str, dict[str, Any]] = {
    "page-agent": {
        # Alibaba Page Agent — real npm package is @page-agent/mcp
        # (verified https://github.com/alibaba/page-agent/tree/main/packages/mcp).
        # One-click flow: ``bridge: "oct"`` auto-fills LLM_BASE_URL
        # to our local /api/oct/openai/v1 proxy and forwards the
        # caller's bearer token as LLM_API_KEY. Explicit env in the
        # PUT body still wins if the user wants a custom LLM.
        "command": "npx",
        "args": ["-y", "@page-agent/mcp"],
        "env": {},
        "bridge": "oct",
        # Trailing "_" separator — the bridge does raw concat.
        "name_prefix": "page_",
        "description": (
            "Page Agent (Alibaba) — browser GUI agent. MCP tools: "
            "execute_task / get_status / stop_task. Needs Chrome "
            "extension + Oct account (or explicit LLM env)."
        ),
    },
}


# ═══════════════════════════════════════════════════════════
# Bundle returned by the factory
# ═══════════════════════════════════════════════════════════


@dataclass
class McpRouter:
    """Package the router with the state dicts callers need to
    introspect. Keeps the external ``app.include_router`` pattern
    while still giving app.py's health endpoint a way to count
    live MCP servers."""

    router: Any
    config_state: dict[str, Any] = field(
        default_factory=lambda: {"mcp_servers": {}},
    )
    runtime_state: dict[str, dict[str, Any]] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════


def _mask_client_id(cid: str) -> str:
    """OAuth App client_id 掩码(仅回显首尾,绝不暴露明文 secret)。"""
    if not cid:
        return ""
    return f"{cid[:4]}…{cid[-4:]}" if len(cid) > 8 else "…"


def create_mcp_router(
    *,
    registry: Any,
    initial_mcp_servers: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> McpRouter:
    """Build the MCP router.

    Parameters
    ----------
    registry :
        ``SkillRegistry``. MCP tools get grafted into this at
        enable-time; at disable-time we pop them back out. The
        unregister path reaches into the registry's private
        ``_by_name`` dict because ``SkillRegistry`` doesn't expose
        an ``unregister()`` method — safe, the attribute's been
        stable, but flagged with ``# noqa: SLF001``.
    initial_mcp_servers :
        Optional iterable of ``(name, MCPServerConfig-ish)``
        objects coming from ``stack.config.mcp_servers``. Each
        entry seeds the config_state as ``enabled=True`` with a
        synthesized description — mirrors the pre-split behavior
        so a user's ``config.yaml`` declarations still appear in
        the UI immediately.
    """
    require_fastapi(__name__)

    def _auth_dep(request: Request) -> None:
        # Router-level auth keeps MCP config/trust self-contained: if
        # this router is mounted without app.py's legacy middleware, it
        # still refuses anonymous callers in auth-on deployments.
        #
        # The OAuth callback is exempt: it is reached by the *provider*
        # redirecting the user's browser, which carries no Authorization
        # header — gating it would deadlock the flow in auth-on
        # deployments. Its credential is the single-use, TTL-bounded
        # ``state`` parameter checked in the handler itself.
        if request.url.path.endswith("/api/mcp/oauth/callback"):
            return
        if require_auth and identity_store is None:
            raise HTTPException(401, "identity store required for MCP auth")
        from runtime.adapters.web_auth import _resolve_actor

        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _operator_dep(request: Request) -> None:
        if request.url.path.endswith("/api/mcp/oauth/callback"):
            return
        from runtime.safety.auth.principal import require_operator

        require_operator(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(tags=["mcp"], dependencies=[Depends(_auth_dep)])
    mcp_config_state: dict[str, Any] = {"mcp_servers": {}}
    # Configuration and runtime clients are tenant-owned.  A single process
    # may serve many tenants, so neither map may be keyed only by server name.
    mcp_config_states: dict[str, dict[str, Any]] = {"__legacy__": mcp_config_state}
    mcp_runtime: dict[str, dict[str, Any]] = {}

    def _tenant_id(request: Any) -> str | None:
        principal = getattr(getattr(request, "state", None), "principal", None)
        value = getattr(principal, "tenant_id", None)
        return str(value).strip() if value else None

    def _tenant_key(tenant_id: str | None) -> str:
        return tenant_id or "__legacy__"

    def _runtime_key(name: str, tenant_id: str | None) -> str:
        return f"{_tenant_key(tenant_id)}\x00{name}"

    def _config_for(tenant_id: str | None) -> dict[str, Any]:
        key = _tenant_key(tenant_id)
        if key not in mcp_config_states:
            mcp_config_states[key] = {"mcp_servers": {}}
        return mcp_config_states[key]

    # Seed declared state from config.yaml's mcp_servers list.
    if initial_mcp_servers:
        servers = {}
        for entry in initial_mcp_servers:
            entry_name = getattr(entry, "name", None)
            if not entry_name:
                continue
            cmd = getattr(entry, "command", "")
            args = getattr(entry, "args", None) or []
            servers[entry_name] = {
                "enabled": True,
                "description": f"{cmd} {' '.join(args)}".strip(),
            }
        mcp_config_state["mcp_servers"] = servers

    # ─── Helpers ────────────────────────────────────────────

    def _resolve_oct_bridge_env(
        request: Any,
    ) -> dict[str, str]:
        """Auto-fill LLM_* env pointing the MCP server at our local
        Oct proxy. Lets a user enable Page Agent in one click:
        we re-use their bearer token and the UI's base URL as the
        inner LLM target — no manual env wiring."""
        if request is None:
            return {}
        auth = request.headers.get("authorization") or ""
        # Strip "Bearer " prefix; the MCP server will re-attach it.
        token = auth[7:] if auth.lower().startswith("bearer ") else auth
        # Point at our local Oct proxy — same host:port the
        # request came in on (respects uvicorn port + reverse
        # proxies that set host headers).
        base = str(request.base_url).rstrip("/") + "/api/oct/openai/v1"
        env: dict[str, str] = {"LLM_BASE_URL": base}
        if token and token != "__guest__":
            env["LLM_API_KEY"] = token
        env.setdefault("LLM_MODEL_NAME", "qwen3.5-flash")
        return env

    def _register_runtime_mcp(
        name: str,
        entry: dict[str, Any],
        request: Any = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Spawn an MCP server + graft its tools into the registry.

        Returns a status dict the caller stuffs into the PUT
        response's ``_status`` field so the UI can render an error
        toast on failure (subprocess spawn / missing command /
        import failure) rather than silently doing nothing.
        """
        if registry is None:
            return {"ok": False, "error": "registry not ready"}
        try:
            from runtime.adapters.mcp_client import (
                HttpMCPClient,
                MCPServerConfig,
                PersistentStdioMCPClient,
                register_mcp_tools_as_skills,
            )
            from runtime.adapters.mcp_client.client import (
                HTTP_AVAILABLE,
                STDIO_AVAILABLE,
            )
            from runtime.adapters.mcp_client.trust import get_trust_store
        except ImportError as e:
            return {"ok": False, "error": f"mcp_client import failed: {e}"}

        preset = MCP_PRESETS.get(name, {})
        transport = str(entry.get("transport") or preset.get("transport") or "stdio")
        url = str(entry.get("url") or preset.get("url") or "")
        is_remote = transport in ("http", "sse") or bool(url)
        name_prefix = entry.get("name_prefix") or preset.get("name_prefix") or name
        if tenant_id:
            tenant_tag = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:12]
            name_prefix = f"mcp_t{tenant_tag}_{name_prefix}"

        if is_remote:
            # Remote (streamable-http / SSE) server.
            if not HTTP_AVAILABLE:
                return {"ok": False, "error": "mcp SDK not installed (pip install mcp)"}
            if not url:
                return {"ok": False, "error": f"no url configured for remote MCP {name!r}"}
            config = MCPServerConfig(
                name=name,
                transport=transport if transport in ("http", "sse") else "http",
                url=url,
                headers=dict(entry.get("headers") or {}),
                tenant_id=tenant_id,
            )
            summary: dict[str, Any] = {"transport": transport, "url": url}
        else:
            # Local stdio (subprocess) server.
            if not STDIO_AVAILABLE:
                return {"ok": False, "error": "mcp SDK not installed (pip install mcp)"}
            command = entry.get("command") or preset.get("command")
            args = entry.get("args") or preset.get("args", [])
            # env layers (later wins): preset → account bridge → user env.
            bridge_env: dict[str, str] = {}
            if entry.get("bridge") == "oct" or preset.get("bridge") == "oct":
                bridge_env = _resolve_oct_bridge_env(request)
            env = {
                **preset.get("env", {}),
                **bridge_env,
                **(entry.get("env") or {}),
            }
            if not command:
                return {
                    "ok": False,
                    "error": (f"no command configured for {name!r} (not a known preset)"),
                }
            config = MCPServerConfig(
                name=name,
                command=command,
                args=list(args),
                env=dict(env),
                tenant_id=tenant_id,
                sandbox_dir=(str(entry.get("sandbox_dir")) if entry.get("sandbox_dir") else None),
            )
            summary = {"command": command, "args": list(args), "env": dict(env)}

        before = set(registry.all_names())
        client = None
        try:
            client = HttpMCPClient(config) if is_remote else PersistentStdioMCPClient(config)
            # Production path · enforce user trust approval. The
            # frontend Settings → MCP page surfaces an "Approve" CTA
            # that calls ``/api/mcp/trust``; until then the bridge
            # refuses to register tools from the server.
            register_mcp_tools_as_skills(
                registry,
                client,
                name_prefix=name_prefix,
                require_trust=True,
                server_name=name,
                tenant_id=tenant_id,
                trust_store=get_trust_store(tenant_id),
            )
        except Exception as e:  # noqa: BLE001
            # Spawn / connection can fail in dozens of ways (missing
            # binary, npm network error, bad url, handshake timeout) ·
            # bundle them into the UI-visible error string rather than
            # fail the whole PUT.
            if client is not None:
                with contextlib.suppress(Exception):
                    client.close()
            return {"ok": False, "error": f"register failed: {e}"}
        after = set(registry.all_names())
        added = sorted(after - before)
        mcp_runtime[_runtime_key(name, tenant_id)] = {
            "skills": added,
            "server_name": name,
            "tenant_id": tenant_id,
            "name_prefix": name_prefix,
            "client": client,
            **summary,
        }
        return {"ok": True, "registered": added}

    def _unregister_runtime_mcp(name: str, tenant_id: str | None = None) -> dict[str, Any]:
        record = mcp_runtime.pop(_runtime_key(name, tenant_id), None)
        if not record or registry is None:
            return {"ok": True, "removed": []}
        removed: list[str] = []
        for skill_name in record.get("skills") or []:
            # SkillRegistry doesn't expose unregister(); pop the
            # internal dict directly. The attribute is part of the
            # public-for-internal-callers API · stable enough.
            if skill_name in registry._by_name:  # noqa: SLF001
                del registry._by_name[skill_name]
                removed.append(skill_name)
        client = record.get("client")
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()
        return {"ok": True, "removed": removed}

    # ─── Endpoints ──────────────────────────────────────────

    @router.get("/api/mcp/config")
    def api_mcp_config(request: Request) -> dict[str, Any]:
        return _config_for(_tenant_id(request))

    @router.put("/api/mcp/config", dependencies=[Depends(_operator_dep)])
    def api_mcp_config_update(
        body: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        tenant_id = _tenant_id(request)
        config_state = _config_for(tenant_id)
        servers = body.get("mcp_servers")
        if not isinstance(servers, dict):
            raise HTTPException(400, "mcp_servers must be an object")
        normalized: dict[str, Any] = {}
        status: dict[str, Any] = {}
        for name, payload in servers.items():
            if not isinstance(name, str) or not isinstance(payload, dict):
                continue
            enabled = bool(payload.get("enabled", False))
            entry: dict[str, Any] = {
                "enabled": enabled,
                "description": str(
                    payload.get("description") or MCP_PRESETS.get(name, {}).get("description") or ""
                ),
            }
            # Pass-through known fields (stdio: command/args/env;
            # remote: transport/url/headers).
            for key in (
                "command",
                "args",
                "env",
                "name_prefix",
                "bridge",
                "transport",
                "url",
                "headers",
            ):
                if key in payload:
                    entry[key] = payload[key]
            normalized[name] = entry

            was_enabled = _runtime_key(name, tenant_id) in mcp_runtime
            if enabled and not was_enabled:
                status[name] = _register_runtime_mcp(name, entry, request, tenant_id)
                # Bubble errors to the entry so the UI can render.
                if not status[name].get("ok"):
                    entry["enabled"] = False
                    entry["error"] = status[name].get("error")
            elif not enabled and was_enabled:
                status[name] = _unregister_runtime_mcp(name, tenant_id)
        config_state["mcp_servers"] = normalized
        return {**config_state, "_status": status}

    # ─── Trust management ─────────────────────────────────

    @router.get("/api/mcp/trust")
    def api_mcp_trust_list(request: Request) -> dict[str, Any]:
        """List all MCP trust entries · UI renders approval chips."""
        from runtime.adapters.mcp_client.trust import get_trust_store

        store = get_trust_store(_tenant_id(request))
        return {
            "entries": [
                {
                    "server_name": e.server_name,
                    "approved": e.approved,
                    "added_ts": e.added_ts,
                    "tool_digest": e.tool_digest,
                    "note": e.note,
                }
                for e in store.list_all()
            ],
        }

    @router.post("/api/mcp/trust", dependencies=[Depends(_operator_dep)])
    def api_mcp_trust_approve(body: dict[str, Any], request: Request) -> dict[str, Any]:
        name = str(body.get("server_name") or "").strip()
        if not name:
            raise HTTPException(400, "server_name required")
        tool_names = body.get("tool_names") or []
        if not isinstance(tool_names, list):
            raise HTTPException(400, "tool_names must be a list")
        note = str(body.get("note") or "")
        from runtime.adapters.mcp_client.trust import get_trust_store

        entry = get_trust_store(_tenant_id(request)).approve(
            name,
            [str(t) for t in tool_names],
            note=note,
        )
        return {
            "ok": True,
            "entry": {
                "server_name": entry.server_name,
                "approved": entry.approved,
                "added_ts": entry.added_ts,
                "tool_digest": entry.tool_digest,
                "note": entry.note,
            },
        }

    # ─── OAuth (remote MCP servers that require it) ────────
    #
    # runtime/adapters/mcp_client/oauth.py (PKCE authorize/token/store) and
    # oauth_discovery.py (RFC 9728/8414/7591 endpoint discovery + dynamic
    # client registration) existed with full test coverage on the core
    # module but were never wired to a route — a server needing OAuth had
    # no way to get authorized. These three endpoints close that gap:
    #   1. POST   /api/mcp/oauth/authorize  · discover + kick off PKCE
    #   2. GET    /api/mcp/oauth/callback   · provider redirects here
    #   3. GET    /api/mcp/oauth/status     · UI polls "authorized?"
    # HttpMCPClient._transport() (client.py) reads the resulting token via
    # ``bearer_for_server(config.name)`` on every connect.

    def _oauth_redirect_uri(request: Request) -> str:
        return str(request.base_url).rstrip("/") + "/api/mcp/oauth/callback"

    @router.post("/api/mcp/oauth/authorize", dependencies=[Depends(_operator_dep)])
    def api_mcp_oauth_authorize(
        body: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        server = str(body.get("server") or "").strip()
        url = str(body.get("url") or "").strip()
        if not server:
            raise HTTPException(400, "server required")
        if not url:
            raise HTTPException(400, "url required")

        from runtime.adapters.mcp_client import oauth, oauth_discovery, oauth_providers

        redirect_uri = _oauth_redirect_uri(request)
        tenant_id = _tenant_id(request)
        store = oauth.get_oauth_store(tenant_id)

        # 1) 标准 MCP OAuth:先做 .well-known 发现(PKCE + 动态客户端注册)。
        endpoints = oauth_discovery.discover(url)
        if endpoints is not None:
            client_id = store.get_client(endpoints.issuer)
            if not client_id and endpoints.registration_url:
                client_id = oauth_discovery.register_client(
                    endpoints.registration_url,
                    redirect_uri=redirect_uri,
                )
                if client_id:
                    store.save_client(endpoints.issuer, client_id)
            if not client_id:
                raise HTTPException(
                    400,
                    "no client_id available for this server — it doesn't "
                    "support dynamic client registration; configure headers "
                    "with a manually-issued token instead.",
                )

            verifier, challenge = oauth.new_pkce()
            state = store.start_pending(
                server=server,
                code_verifier=verifier,
                redirect_uri=redirect_uri,
                token_url=endpoints.token_url,
                client_id=client_id,
            )
            authorize_url = oauth.build_authorize_url(
                authorize_url=endpoints.authorize_url,
                client_id=client_id,
                redirect_uri=redirect_uri,
                scopes=list(endpoints.scopes) or None,
                state=state,
                code_challenge=challenge,
            )
            # TongDaXin's standards metadata is valid, but its hosted consent
            # page currently hands the result to a vendor desktop protocol
            # instead of navigating to the registered loopback URI. The
            # Echo desktop OAuth popup bridges that protocol; ordinary web
            # browsers cannot safely intercept it.
            desktop_callback_required = endpoints.authorize_url.startswith(
                "https://auth.tdx.com.cn/tdx-oauth/",
            )
            return {
                "ok": True,
                "authorize_url": authorize_url,
                "callback_transport": (
                    "desktop-deep-link" if desktop_callback_required else "standard"
                ),
            }

        # 2) 服务商直连 OAuth App(GitHub / GitLab 等 WorkBuddy server-side 连接器):
        #    用户在自己账号下注册 OAuth App(client_id/secret 加密存本地),授权页
        #    回调到我们后端换 token —— 和 WorkBuddy 靠它平台 OAuth App 登录一个原理。
        provider_id = str(body.get("provider") or "").strip() or None
        prov = oauth_providers.get_provider(provider_id) if provider_id else None
        if prov is None:
            raise HTTPException(
                400,
                "OAuth discovery failed — the server may not support OAuth, "
                "or the well-known metadata endpoints are unreachable.",
            )

        app = store.get_app_client(prov.id)
        if not app:
            # 还没配置 OAuth App 凭据:让前端弹窗引导用户创建并填写。
            return {
                "ok": False,
                "needs_app_credentials": True,
                "provider": prov.id,
                "provider_name": prov.name,
                "authorize_url": prov.authorize_url,
                "token_url": prov.token_url,
                "scopes": list(prov.scopes),
                "docs_url": prov.docs_url,
                "redirect_uri": redirect_uri,
                "requires_client_secret": prov.requires_client_secret,
            }
        client_id = str(app.get("client_id") or "")
        if not client_id:
            raise HTTPException(400, "OAuth App client_id 缺失,请重新配置凭据。")
        state = store.start_pending(
            server=server,
            code_verifier="",
            redirect_uri=redirect_uri,
            token_url=prov.token_url,
            client_id=client_id,
            client_secret=str(app.get("client_secret") or ""),
            use_pkce=False,
        )
        authorize_url = oauth.build_authorize_url(
            authorize_url=prov.authorize_url,
            client_id=client_id,
            redirect_uri=redirect_uri,
            scopes=list(prov.scopes) or None,
            state=state,
            code_challenge=None,
        )
        return {"ok": True, "authorize_url": authorize_url, "provider": prov.id}

    @router.get("/api/mcp/oauth/callback")
    def api_mcp_oauth_callback(
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> Any:
        from fastapi.responses import HTMLResponse

        def _page(message: str, *, ok: bool) -> HTMLResponse:
            # Standard OAuth-popup pattern: the frontend opens
            # authorize_url in a popup; this page is the redirect
            # target, so it posts the result to the opener and closes
            # itself rather than leaving a dead tab open.
            safe = message.replace("<", "&lt;").replace(">", "&gt;")
            status = "ok" if ok else "error"
            return HTMLResponse(f"""<!doctype html><html><body>
<p>{safe}</p>
<script>
try {{
  window.opener && window.opener.postMessage(
    {{ source: "echo-mcp-oauth", status: "{status}" }}, "*"
  );
}} catch (e) {{}}
window.close();
</script>
</body></html>""")

        if not state:
            return _page("Missing code/state in callback.", ok=False)

        from runtime.adapters.mcp_client import oauth

        store = oauth.get_oauth_store_for_state(state)
        pending = store.pop_pending(state)
        if pending is None:
            return _page(
                "Invalid or expired authorization request — please retry.",
                ok=False,
            )
        # Provider errors are still callbacks for a specific authorization
        # attempt. Consume the pending state before showing the failure so a
        # captured/deep-linked cancellation cannot be replayed later.
        if error:
            return _page(f"Authorization failed: {error}", ok=False)
        if not code:
            return _page("Missing code/state in callback.", ok=False)
        try:
            token_response = oauth.exchange_code(
                token_url=pending.token_url,
                code=code,
                code_verifier=pending.code_verifier if pending.use_pkce else None,
                client_id=pending.client_id,
                client_secret=pending.client_secret or None,
                redirect_uri=pending.redirect_uri,
            )
        except Exception as exc:  # noqa: BLE001 — surface to the popup, not a 500
            return _page(f"Token exchange failed: {exc}", ok=False)

        store.save_tokens(
            pending.server,
            token_response,
            token_url=pending.token_url,
            client_id=pending.client_id,
        )
        return _page(f"Authorized {pending.server}. You can close this tab.", ok=True)

    @router.get("/api/mcp/oauth/status")
    def api_mcp_oauth_status(server: str, request: Request) -> dict[str, Any]:
        from runtime.adapters.mcp_client import oauth

        return {
            "server": server,
            "authorized": oauth.get_oauth_store(_tenant_id(request)).has_tokens(server),
        }

    @router.delete("/api/mcp/oauth/{server_name}", dependencies=[Depends(_operator_dep)])
    def api_mcp_oauth_forget(server_name: str, request: Request) -> dict[str, Any]:
        from runtime.adapters.mcp_client import oauth

        return {"ok": oauth.get_oauth_store(_tenant_id(request)).forget(server_name)}

    # ── 服务商 OAuth App 凭据管理(BYO OAuth)──────────────
    # GitHub / GitLab 等不暴露 .well-known 元数据的连接器,靠用户自己注册的
    # OAuth App(client_id + client_secret)完成网页登录。凭据与 token 一起
    # 加密存到 ~/.echo/mcp_oauth.json,接口不返回明文 secret。

    @router.get("/api/mcp/oauth/app/{provider}")
    def api_mcp_oauth_app_get(provider: str, request: Request) -> dict[str, Any]:
        from runtime.adapters.mcp_client import oauth, oauth_providers

        prov = oauth_providers.get_provider(provider)
        if prov is None:
            raise HTTPException(404, f"unknown oauth provider: {provider}")
        app = oauth.get_oauth_store(_tenant_id(request)).get_app_client(prov.id)
        cid = (app or {}).get("client_id", "")
        return {
            "provider": prov.id,
            "provider_name": prov.name,
            "has_app": bool(app and cid),
            "configured": bool(app and cid),
            "client_id_masked": _mask_client_id(cid),
        }

    @router.post("/api/mcp/oauth/app/{provider}", dependencies=[Depends(_operator_dep)])
    def api_mcp_oauth_app_save(
        provider: str,
        body: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        from runtime.adapters.mcp_client import oauth, oauth_providers

        prov = oauth_providers.get_provider(provider)
        if prov is None:
            raise HTTPException(404, f"unknown oauth provider: {provider}")
        client_id = str(body.get("client_id") or "").strip()
        client_secret = str(body.get("client_secret") or "").strip()
        if not client_id:
            raise HTTPException(400, "client_id required")
        if prov.requires_client_secret and not client_secret:
            raise HTTPException(400, f"{prov.name} 需要 client_secret")
        oauth.get_oauth_store(_tenant_id(request)).save_app_client(
            prov.id,
            client_id,
            client_secret,
        )
        return {
            "ok": True,
            "provider": prov.id,
            "provider_name": prov.name,
            "client_id_masked": _mask_client_id(client_id),
        }

    @router.delete("/api/mcp/oauth/app/{provider}", dependencies=[Depends(_operator_dep)])
    def api_mcp_oauth_app_delete(provider: str, request: Request) -> dict[str, Any]:
        from runtime.adapters.mcp_client import oauth, oauth_providers

        prov = oauth_providers.get_provider(provider)
        if prov is None:
            raise HTTPException(404, f"unknown oauth provider: {provider}")
        ok = oauth.get_oauth_store(_tenant_id(request)).forget_app_client(prov.id)
        return {"ok": ok, "provider": prov.id, "provider_name": prov.name}

    @router.delete("/api/mcp/trust/{server_name}", dependencies=[Depends(_operator_dep)])
    def api_mcp_trust_revoke(server_name: str, request: Request) -> dict[str, Any]:
        """Revoke approval · tools drop out immediately.

        The trust store flag is the source of truth for *future*
        registrations, but stale runtime state has to come down too:
        without an unregister, the in-memory ``mcp_runtime`` entry
        keeps its skill closures alive and ``ToolExecutor`` will keep
        dispatching into a now-untrusted MCP subprocess until the next
        process restart. We synchronously stop the runtime entry so
        that revocation actually takes effect.
        """
        from runtime.adapters.mcp_client.trust import get_trust_store

        tenant_id = _tenant_id(request)
        revoked = get_trust_store(tenant_id).revoke(server_name)
        runtime_status: dict[str, Any] | None = None
        if _runtime_key(server_name, tenant_id) in mcp_runtime:
            try:
                runtime_status = _unregister_runtime_mcp(server_name, tenant_id)
            except Exception as exc:  # noqa: BLE001
                runtime_status = {"error": str(exc)}
        return {
            "ok": revoked,
            "server_name": server_name,
            "runtime": runtime_status,
        }

    return McpRouter(
        router=router,
        config_state=mcp_config_state,
        runtime_state=mcp_runtime,
    )


__all__ = ["MCP_PRESETS", "McpRouter", "create_mcp_router"]
