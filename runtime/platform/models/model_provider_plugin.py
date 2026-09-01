"""Lifecycle bridge for plugin-provided OpenAI-compatible model gateways.

The connector registry owns installation and encrypted credentials.  The
config router owns live model routes.  This module is the deliberately small
bridge between them: validate a provider key, turn a declarative plugin
descriptor into a custom-model entry, and remove that entry again when the
plugin is disabled or uninstalled.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def model_provider_credential_ref(connector_id: str, key: str = "api_key") -> str:
    """Return a non-secret reference stored in ``custom_models.json``."""

    return f"connector:{connector_id}:{key}"


def resolve_model_provider_api_key(
    entry: dict[str, Any],
    *,
    credential_store: Any = None,
) -> str:
    """Resolve an API key without ever copying it into model configuration."""

    inline = str(entry.get("api_key") or "")
    if inline:
        return inline
    reference = str(entry.get("credential_ref") or "")
    parts = reference.split(":", 2)
    if len(parts) != 3 or parts[0] != "connector" or not parts[1] or not parts[2]:
        return ""
    if credential_store is None:
        from runtime.platform.connectors.credential_store import CredentialStore

        credential_store = CredentialStore()
    return str(credential_store.get_secret(parts[1], parts[2]) or "")


def model_provider_entry_has_key(
    entry: dict[str, Any],
    *,
    credential_store: Any = None,
) -> bool:
    """Return key presence while keeping the secret inside the credential store."""

    if entry.get("api_key") or entry.get("credential_configured") is True:
        return True
    reference = str(entry.get("credential_ref") or "")
    parts = reference.split(":", 2)
    if len(parts) != 3 or parts[0] != "connector" or not parts[1] or not parts[2]:
        return False
    if credential_store is None:
        from runtime.platform.connectors.credential_store import CredentialStore

        credential_store = CredentialStore()
    return parts[2] in set(credential_store.list_secrets(parts[1]))


class ModelProviderPluginManager:
    """Synchronize one installed model-provider plugin with live model routes."""

    def __init__(
        self,
        *,
        custom_models: dict[str, dict[str, Any]],
        lock: threading.RLock,
        save: Callable[..., None],
        unregister_entry: Callable[..., bool],
        rebuild_routes: Callable[[], dict[str, dict[str, Any]]],
        credential_store: Any = None,
    ) -> None:
        self._custom_models = custom_models
        self._lock = lock
        self._save = save
        self._unregister_entry = unregister_entry
        self._rebuild_routes = rebuild_routes
        self._credential_store = credential_store

    @staticmethod
    def _descriptor(item: dict[str, Any]) -> dict[str, Any]:
        descriptor = item.get("model_provider")
        if not isinstance(descriptor, dict) or not descriptor:
            raise ValueError("capability is not a model-provider plugin")
        return descriptor

    @staticmethod
    def _entry_id(item: dict[str, Any], descriptor: dict[str, Any]) -> str:
        return str(descriptor.get("entry_id") or item.get("id") or "").strip()

    @staticmethod
    def _provider_name(item: dict[str, Any], descriptor: dict[str, Any]) -> str:
        return str(
            descriptor.get("display_name_zh")
            or descriptor.get("display_name")
            or item.get("name_zh")
            or item.get("name")
            or "模型服务"
        ).strip()

    @staticmethod
    def _normalize_base_url(value: str) -> str:
        raw = value.strip().rstrip("/")
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("服务地址必须是有效的 HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("服务地址不能包含账号、查询参数或片段")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("远程模型服务必须使用 HTTPS；HTTP 仅允许本机地址")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))

    def _resolve_base_url(
        self,
        item: dict[str, Any],
        descriptor: dict[str, Any],
        tokens: dict[str, str],
    ) -> str:
        configured = str(tokens.get("base_url") or "").strip()
        if not configured and descriptor.get("configurable_base_url"):
            store = self._credential_store
            if store is None:
                from runtime.platform.connectors.credential_store import CredentialStore

                store = CredentialStore()
            configured = str(store.get_secret(str(item.get("id") or ""), "base_url") or "")
        return self._normalize_base_url(configured or str(descriptor.get("base_url") or ""))

    def validate(
        self,
        item: dict[str, Any],
        *,
        tokens: dict[str, str] | None,
    ) -> dict[str, Any]:
        """Validate the submitted key and discover currently available free models."""

        descriptor = self._descriptor(item)
        tokens = tokens or {}
        provider_name = self._provider_name(item, descriptor)
        api_key = str(tokens.get("api_key") or tokens.get("access_token") or "").strip()
        if not api_key:
            store = self._credential_store
            if store is None:
                from runtime.platform.connectors.credential_store import CredentialStore

                store = CredentialStore()
            api_key = str(store.get_secret(str(item.get("id") or ""), "api_key") or "")
        if not api_key:
            raise ValueError(f"请先填写 {provider_name} API Key")

        base_url = self._resolve_base_url(item, descriptor, tokens)
        endpoint = (
            f"{base_url}/models"
            if descriptor.get("configurable_base_url")
            else str(descriptor.get("models_endpoint") or "").strip()
        )
        if not endpoint:
            raise ValueError("model-provider plugin has no models endpoint")

        try:
            import httpx

            response = httpx.get(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=float(descriptor.get("probe_timeout_seconds") or 15),
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - mapped to a bounded user-facing error
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {401, 403}:
                raise ValueError(f"{provider_name} API Key 无效或没有访问权限") from exc
            raise ValueError(f"暂时无法连接 {provider_name}，请检查服务地址和网络后重试") from exc

        rows = payload.get("data") if isinstance(payload, dict) else None
        available = {
            str(row.get("id") or "").strip()
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict) and str(row.get("id") or "").strip()
        }
        configured = [
            str(model).strip()
            for model in (descriptor.get("free_models") or [])
            if str(model or "").strip()
        ]
        excluded = {
            str(model).strip()
            for model in (descriptor.get("excluded_models") or [])
            if str(model or "").strip()
        }
        # Preserve the reviewed list's order, then append newly published
        # ``*-free`` models automatically. Explicit exclusions remain available
        # for providers that publish a model before its wire protocol is
        # supported by this adapter.
        discovered = sorted(
            model
            for model in available
            if (descriptor.get("discover_all_models") or model.endswith("-free"))
            and model not in excluded
        )
        models = [model for model in configured if model in available and model not in excluded]
        models.extend(model for model in discovered if model not in models)
        if not models:
            raise ValueError(f"当前账号没有检测到可用的 {provider_name} 模型")
        return {
            "models": models,
            "available_count": len(available),
            "base_url": base_url,
        }

    def configure(
        self,
        item: dict[str, Any],
        *,
        models: list[str] | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        """Persist a secret-free model entry and hot-register its routes."""

        descriptor = self._descriptor(item)
        entry_id = self._entry_id(item, descriptor)
        if not entry_id:
            raise ValueError("model-provider plugin has no entry id")
        selected = [
            str(model).strip()
            for model in (models or descriptor.get("free_models") or [])
            if str(model or "").strip()
        ]
        if not selected:
            raise ValueError("model-provider plugin has no configured models")
        connector_id = str(item.get("id") or "").strip()
        entry = {
            "id": entry_id,
            "name": str(descriptor.get("display_name") or item.get("name") or entry_id),
            "display_name": str(
                descriptor.get("display_name_zh")
                or descriptor.get("display_name")
                or item.get("name_zh")
                or entry_id
            ),
            "provider": "openai-compatible",
            "base_url": self._normalize_base_url(base_url or str(descriptor.get("base_url") or "")),
            "api_key": "",
            "credential_ref": model_provider_credential_ref(connector_id),
            "managed_by_plugin": connector_id,
            "models": selected,
            "context_window": int(descriptor.get("context_window") or 256_000),
            "enable_1m_context": False,
            "supports_thinking": bool(descriptor.get("supports_thinking", False)),
            "supports_vision": bool(descriptor.get("supports_vision", False)),
            "supports_tool_use": bool(descriptor.get("supports_tool_use", True)),
            "compat_profile": str(descriptor.get("compat_profile") or "openai_compat"),
            "responses_models": [
                str(model).strip()
                for model in (descriptor.get("responses_models") or [])
                if str(model or "").strip() in selected
            ],
            "default_reasoning_effort": None,
            "default_headers": {},
        }
        if not model_provider_entry_has_key(
            entry,
            credential_store=self._credential_store,
        ):
            raise ValueError("请先连接插件并填写服务商 API Key")
        entry["credential_configured"] = True
        with self._lock:
            previous = self._custom_models.get(entry_id)
            if previous:
                self._unregister_entry(previous, fallback_id=entry_id)
            self._custom_models[entry_id] = entry
            self._save(entry_id)
            status = self._rebuild_routes().get(entry_id, {"ok": False})
        if not status.get("ok"):
            raise RuntimeError(str(status.get("error") or "模型路由注册失败"))
        return {
            "configured": True,
            "entry_id": entry_id,
            "models": selected,
        }

    def remove(self, item: dict[str, Any]) -> dict[str, Any]:
        """Remove only the model entry owned by this plugin."""

        descriptor = self._descriptor(item)
        entry_id = self._entry_id(item, descriptor)
        with self._lock:
            current = self._custom_models.get(entry_id)
            if not isinstance(current, dict) or current.get("managed_by_plugin") != item.get("id"):
                return {"removed": False, "entry_id": entry_id}
            self._custom_models.pop(entry_id, None)
            self._unregister_entry(current, fallback_id=entry_id)
            self._save(entry_id)
            self._rebuild_routes()
        return {"removed": True, "entry_id": entry_id}


__all__ = [
    "ModelProviderPluginManager",
    "model_provider_credential_ref",
    "model_provider_entry_has_key",
    "resolve_model_provider_api_key",
]
