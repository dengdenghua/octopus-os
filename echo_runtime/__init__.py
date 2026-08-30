"""echo-runtime — 资产 registry 消费端 SDK(capability-plane.md §B 的雏形)。

thin client:**拉取 → 验签 → 落地到产品现有磁盘布局**,再绑产品自己的 runtime。
读/解析/落地半边**永不 import 产品 runtime**(零耦合),故可被任何产品(agent / mobile / os)vendor。
执行代码(skill handler / plugin)永远留在各产品本地——只共享 data-kind 资产,不过线执行码。

用法:``python -m echo_runtime list`` / ``python -m echo_runtime sync <slug> --skills-dir skills/public``。
"""

from .bootstrap import bootstrap_skills, read_lockfile, write_lockfile
from .client import (
    DEFAULT_BUNDLE_RESPONSE_MAX_BYTES,
    DEFAULT_JSON_RESPONSE_MAX_BYTES,
    DEFAULT_SKILL_RESPONSE_MAX_BYTES,
    AssetContent,
    AssetPayload,
    RegistryAsset,
    RegistryClient,
    RegistryResponseTooLarge,
    safe_registry_asset_id,
    safe_registry_skill_slug,
)
from .materialize import SAFE_TYPES, materialize_skill, sync_skills

__all__ = [
    "RegistryClient",
    "RegistryResponseTooLarge",
    "RegistryAsset",
    "AssetPayload",
    "AssetContent",
    "DEFAULT_JSON_RESPONSE_MAX_BYTES",
    "DEFAULT_SKILL_RESPONSE_MAX_BYTES",
    "DEFAULT_BUNDLE_RESPONSE_MAX_BYTES",
    "materialize_skill",
    "sync_skills",
    "SAFE_TYPES",
    "bootstrap_skills",
    "read_lockfile",
    "write_lockfile",
    "safe_registry_asset_id",
    "safe_registry_skill_slug",
]
