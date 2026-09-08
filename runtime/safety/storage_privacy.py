"""The Storage gateway projects the same policy as Agent inference."""

from __future__ import annotations

import re
from typing import Any

from runtime.core.cerebrum.ai_mode import current_ai_mode
from runtime.safety.privacy import PrivacyViolation, is_loopback_endpoint


def storage_policy() -> dict[str, Any]:
    mode = current_ai_mode()
    return {
        "mode": mode,
        "allow_cloud_answering": mode == "efficiency",
        "allow_snippet_export": mode == "efficiency",
        "max_exported_snippets": 8,
        "max_snippet_chars": 600,
        "redact_file_paths_for_cloud": True,
    }


def verify_private_storage(policy: Any, models: Any) -> None:
    """Require the local service to acknowledge policy before receiving data.

    In-process model implementations and the local service itself are trusted
    deployment components. Unknown response shapes/configuration fail closed.
    """
    if not isinstance(policy, dict) or not (
        policy.get("mode") == "privacy"
        and policy.get("allow_cloud_answering") is False
        and policy.get("allow_snippet_export") is False
    ):
        raise PrivacyViolation("本地数据库未确认隐私策略，已停止检索和分析。")
    if not isinstance(models, list):
        raise PrivacyViolation("无法验证本地数据库模型配置，已停止检索和分析。")
    for model in models:
        if not isinstance(model, dict):
            raise PrivacyViolation("本地数据库模型配置无效。")
        if model.get("status") == "not_configured":
            continue
        endpoint = model.get("endpoint")
        local = is_loopback_endpoint(endpoint) if endpoint else model.get("provider") == "local"
        if not local:
            raise PrivacyViolation("本地数据库配置了外部模型，隐私模式下已停止检索和分析。")


def storage_compute_request(method: str, path: str) -> bool:
    # Only known metadata/file reads bypass inference admission. Unknown GET
    # routes may start computation too; their HTTP verb is not proof of safety.
    known_read = re.fullmatch(
        r"v1/(manifest|models|sources|browse|albums|apps|files|index/jobs(?:/[^/]+)?)"
        r"|v1/files/[^/]+/(content|thumbnail|preview|text)",
        path,
    )
    return method.upper() not in {"GET", "HEAD"} or known_read is None
