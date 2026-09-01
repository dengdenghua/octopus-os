"""资产 Registry 消费路由(母体接 registry · 只读浏览 + 安装 prompt-skill)。

把 echo-runtime SDK([echo_runtime])包成 HTTP 面:让母体前端/客户端能浏览公网 registry 的技能、
按需安装(下载→验签→落地 ``skills/public/<slug>/SKILL.md``→**运行时热注册**,无需重启)。

- GET  /api/registry/skills                列 registry 技能(search/category/offset/limit)
- GET  /api/registry/skills/{slug}         单技能详情(信封 + body 预览)
- POST /api/registry/skills/{slug}/install 安装:sync 落地 + 运行时注册

设计边界:**只装 prompt-pack(type=skill,body 当 prompt 注入、从不执行)**;执行型资产不过此路由。
additive 新文件,零碰 Codex WIP(cowork/oct/i18n);SDK 读/解析半边仍 runtime-free,本路由是产品侧 glue。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tarfile
import tempfile
import time as _time
from io import BytesIO
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request

    FASTAPI_AVAILABLE = True
except Exception:  # pragma: no cover - fastapi optional at import time
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    Depends = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

# registry 除 skill 外还托管 role / twin-role(数字分身岗位模板)/ plugin / task /
# twin / experience(见 asset.type,echo.aurest.ai 实测 473 条)。角色类(role/
# twin-role)与 skill 同属 kind=data 声明式 prompt,落地规则等价 → 直接可"安装"成
# 本地可用 agent(同 enterprise_assets_router._scaffold_local_agent 的落地形状)。
# plugin 类目前统一标 kind=code(codex-plugin 集成说明)。它们的 registry body
# 是提示词/能力说明，不是可直接执行的插件包；安装时只把 body 落地为本地
# prompt-skill，不下载、导入或执行远程代码。真正的本地代码插件仍需走本地
# 插件目录和显式权限审核。
_ROLE_ASSET_TYPES = ("role", "twin-role")
_MAX_PLUGIN_UNCOMPRESSED_BYTES = 200 * 1024 * 1024


# registry 全量列表查询是 5 秒级慢请求(公网),列表端点加进程内 TTL 缓存:
# 同一批资产在 TTL 内命中内存,search/category/分页都在缓存之上做;``refresh=1`` 强制穿透拉新。
# 只缓存成功结果(loader 抛异常时不写入,下次仍会重试),零第三方依赖。
_REGISTRY_CACHE_TTL_SECONDS = 60.0
_REGISTRY_CACHE: dict[str, tuple[float, list[Any]]] = {}


def _registry_list_cached(
    key: str,
    loader: Any,
    *,
    refresh: bool = False,
) -> list[Any]:
    now = _time.monotonic()
    if not refresh:
        hit = _REGISTRY_CACHE.get(key)
        if hit is not None and (now - hit[0]) < _REGISTRY_CACHE_TTL_SECONDS:
            return hit[1]
    assets = loader()
    _REGISTRY_CACHE[key] = (now, assets)
    return assets


def _ensure_safe_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"agent scaffold path must not be a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"agent scaffold path must be a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _atomic_write_text(path: Path, content: str) -> None:
    _ensure_safe_dir(path.parent)
    tmp: Path | None = None
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as f:
        tmp = Path(f.name)
        f.write(content)
        f.flush()
    try:
        tmp.replace(path)
    except Exception:
        if tmp is not None and tmp.exists():
            tmp.unlink()
        raise


def _scaffold_local_agent_from_registry_asset(asset: Any) -> tuple[str, Path]:
    """把 registry role/twin-role 资产落地成本地 agent(profile.jsonc + agent-core/*)。

    形状对齐 enterprise_assets_router._scaffold_local_agent:body 当 SOUL,
    写入 default_agents_root()/registry_<slug>/,下次 /api/agents 扫描即可见。
    """
    from runtime.execution.agents.loader import default_agents_root

    slug = str(getattr(asset, "slug", "") or asset.id.split("/")[-1])
    agent_id = f"registry_{re.sub(r'[^a-z0-9_]+', '_', slug.lower()).strip('_') or 'imported_role'}"
    name = asset.name or slug
    category = asset.category or "specialist"
    tags = list(asset.tags or [])
    description = asset.description or ""
    body = asset.body or ""

    agent_root = default_agents_root() / agent_id
    core = agent_root / "agent-core"
    _ensure_safe_dir(agent_root)
    _ensure_safe_dir(core)
    from runtime.execution.agents.identity import build_identity_profile, generate_identity_code

    identity_code = generate_identity_code(default_agents_root())
    profile = {
        "id": agent_id,
        "name": name,
        "icon": "🌐",
        "did": identity_code,
        "identity_code": identity_code,
        "identity": build_identity_profile(identity_code),
        "description": description,
        "category": category,
        "tags": tags,
        "model": {"provider": "auto", "name": "auto"},
        "runtime": "local",
        "capabilities": {"execution_backend": "codex_app_server"},
        "source": "registry",
    }
    _atomic_write_text(
        agent_root / "profile.jsonc", json.dumps(profile, ensure_ascii=False, indent=2)
    )
    soul = body or (
        f"You are {name}.\n\nPrimary mission: {description}\n\n"
        f"Specialties: {', '.join(tags)}.\nBe concise, action-oriented, and precise."
    )
    _atomic_write_text(core / "SOUL.md", soul)
    _atomic_write_text(
        core / "IDENTITY.md",
        f"- Name: {name}\n- Role: {category} specialist\n- Source: registry asset library\n",
    )
    _atomic_write_text(
        core / "tool-registry.jsonc",
        json.dumps(
            {"arms": ["fs_writer", "git", "shell"], "extra_affinity": tags, "private_skills": []},
            ensure_ascii=False,
            indent=2,
        ),
    )
    try:
        from runtime.execution.misc.agent_avatar import write_pixel_agent_avatar

        write_pixel_agent_avatar(agent_root / "avatar.svg", name)
    except Exception:  # noqa: BLE001 - 头像生成失败不阻断安装
        pass
    return agent_id, agent_root


def _asset_type(asset_id: str) -> str:
    return asset_id.split("/", 1)[0]


def _plugin_branding_index(
    plugin_root: Path,
    publisher_trust_store_path: Path | None,
) -> dict[str, dict[str, Any]]:
    """Index trusted local plugin branding for the public registry cards.

    The public registry envelope intentionally contains no executable plugin
    files and often only has an emoji ``icon``.  When the same plugin is
    installed locally, its signed/validated Codex manifest is the source of
    truth for the official logo and brand colour.  Matching is deliberately
    limited to normalized ids/names and never downloads arbitrary image URLs.
    """
    try:
        from runtime.platform.plugins.codex_discovery import discover_codex_plugins

        plugins = discover_codex_plugins(
            [plugin_root], publisher_trust_store_path=publisher_trust_store_path
        )
    except Exception:  # noqa: BLE001 - branding must never break registry browsing
        return {}

    index: dict[str, dict[str, Any]] = {}
    for plugin in plugins:
        if not isinstance(plugin, dict):
            continue
        keys = (plugin.get("id"), plugin.get("name"), plugin.get("display_name"))
        for value in keys:
            if isinstance(value, str) and value.strip():
                normalized = re.sub(r"[^a-z0-9]+", "", value.lower())
                if normalized:
                    index[normalized] = plugin
    return index


def _enrich_registry_plugin_row(
    row: dict[str, Any],
    asset: Any,
    branding: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    candidates = (getattr(asset, "slug", ""), getattr(asset, "name", ""))
    local: dict[str, Any] | None = None
    for candidate in candidates:
        normalized = re.sub(r"[^a-z0-9]+", "", str(candidate).lower())
        if normalized and normalized in branding:
            local = branding[normalized]
            break
    if local:
        # These URLs are generated by codex_discovery and served by the local
        # /api/plugins asset endpoint; do not trust remote registry URLs.
        for key in ("logo_url", "icon_url", "brand_color"):
            value = local.get(key)
            if value:
                row[key] = value
        row["local_plugin_id"] = local.get("id")
    return row


def _is_installable_role_asset(asset: Any) -> bool:
    return str(getattr(asset, "type", "") or "") in _ROLE_ASSET_TYPES and (
        str(getattr(asset, "kind", "") or "") == "data"
    )


def _materialize_registry_plugin_prompt(asset: Any, skills_root: Path) -> tuple[str, Path]:
    """Install a registry plugin as a prompt-only local capability.

    Public registry plugin assets currently contain a bounded text body rather than
    a signed executable bundle. Keeping that boundary explicit makes the marketplace
    useful without turning a one-click install into remote code execution.
    """
    asset_id = str(getattr(asset, "id", "") or "")
    if _asset_type(asset_id) != "plugin":
        raise ValueError(f"not a plugin asset: {asset_id}")
    slug = str(getattr(asset, "slug", "") or asset_id.split("/", 1)[-1])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", slug):
        raise ValueError(f"unsafe plugin slug from registry payload: {slug!r}")
    if str(getattr(asset, "type", "") or "") != "plugin":
        raise ValueError(f"registry payload type mismatch: {getattr(asset, 'type', '')!r}")

    # Keep installed names disjoint from first-party skills and stable across updates.
    skill_name = f"plugin-{slug}"
    skill_dir = Path(skills_root) / skill_name
    _ensure_safe_dir(Path(skills_root))
    _ensure_safe_dir(skill_dir)

    display_name = " ".join(str(getattr(asset, "name", "") or slug).split())
    description = " ".join(str(getattr(asset, "description", "") or "").split())
    tags = [str(tag).strip() for tag in (getattr(asset, "tags", None) or []) if str(tag).strip()]
    body = str(getattr(asset, "body", "") or "").strip()
    if len(body.encode("utf-8")) > 256 * 1024:
        raise ValueError("registry plugin prompt is too large")
    if not body:
        body = description or f"Use the {display_name} integration when it is available."
    frontmatter = (
        "---\n"
        f"name: {skill_name}\n"
        f"description: {description or display_name}\n"
        "source: registry-plugin\n"
        f"version: {str(getattr(asset, 'version', '') or 'unknown')}\n"
        f"tags: [{', '.join(tags)}]\n"
        "---\n\n"
    )
    _atomic_write_text(skill_dir / "SKILL.md", frontmatter + body + "\n")
    _atomic_write_text(
        skill_dir / "PLUGIN.json",
        json.dumps(
            {
                "id": asset_id,
                "name": display_name,
                "version": getattr(asset, "version", None),
                "source": "registry",
                "execution": "prompt-only",
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    return skill_name, skill_dir / "SKILL.md"


def _install_registry_plugin_bundle(
    asset: Any,
    *,
    client: Any,
    plugin_root: Path,
    publisher_trust_store_path: Path | None,
) -> dict[str, Any]:
    """Download and install a signed plugin bundle through the local lifecycle gate."""
    bundle = getattr(asset, "bundle", None)
    if bundle is None or not getattr(bundle, "ref", None):
        raise ValueError("registry plugin has no installable bundle")
    raw_checksum = str(getattr(bundle, "checksum", "") or "").strip()
    checksum_match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", raw_checksum)
    if checksum_match is None:
        raise ValueError("registry plugin bundle requires a valid sha256 checksum")
    expected = checksum_match.group(1).lower()
    data = client.fetch_bundle(
        str(asset.id),
        expected_size=getattr(bundle, "size", None),
    )
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("registry plugin bundle checksum mismatch")
    declared_size = getattr(bundle, "size", None)
    if declared_size is not None and len(data) != int(declared_size):
        raise ValueError("registry plugin bundle size mismatch")
    if len(data) > 50 * 1024 * 1024:
        raise ValueError("registry plugin bundle is too large")

    from runtime.platform.plugins.plugin_lifecycle import install_local_plugin

    plugin_root = Path(plugin_root).expanduser().resolve(strict=False)
    plugin_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".registry-plugin-", dir=plugin_root.parent) as tmp:
        tmp_root = Path(tmp)
        with tarfile.open(fileobj=BytesIO(data), mode="r:*") as archive:
            members = archive.getmembers()
            if not members:
                raise ValueError("registry plugin bundle is empty")
            total_size = 0
            for member in members:
                path = Path(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError(f"unsafe path in plugin bundle: {member.name}")
                if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                    raise ValueError(f"unsupported entry in plugin bundle: {member.name}")
                total_size += max(0, int(member.size))
                if total_size > _MAX_PLUGIN_UNCOMPRESSED_BYTES:
                    raise ValueError("registry plugin bundle expands beyond the safety limit")

            # Extract regular files ourselves on every supported Python. This
            # keeps the same traversal/link guarantees on 3.11 and avoids the
            # unsafe extractall fallback needed before tarfile filters existed.
            for member in members:
                destination = tmp_root.joinpath(*Path(member.name).parts)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"could not read plugin bundle entry: {member.name}")
                with source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)

        candidates = [
            path.parent.parent
            for path in tmp_root.rglob(".codex-plugin/plugin.json")
            if path.is_file()
        ]
        if len(candidates) != 1:
            raise ValueError("plugin bundle must contain exactly one .codex-plugin/plugin.json")
        return install_local_plugin(
            candidates[0],
            plugin_root=plugin_root,
            publisher_trust_store_path=publisher_trust_store_path,
            confirm_install=True,
            require_trusted_publisher=True,
        )


def _register_runtime(skill_registry: Any, skills_root: Path) -> int:
    """把 skills_root 下的 prompt-skill 注册进**活 registry**(无需重启)。
    已注册的同名会被 register_market_skills 自身跳过,故只净增新装的。"""
    from runtime.execution.suckers.market_skills import immutable_prompt_catalog_required

    if skill_registry is None or not skills_root.is_dir() or immutable_prompt_catalog_required():
        return 0
    try:
        from runtime.execution.suckers.market_skills import register_market_skills

        return int(
            register_market_skills(
                skill_registry,
                all_skills_dir=skills_root,
                respect_enabled_flag=False,
                verify_tests=False,
            )
        )
    except Exception:  # noqa: BLE001 - 注册失败不阻断安装(落地的文件下次启动仍会被加载)
        return 0


def create_registry_consumer_router(
    *,
    skill_registry: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    registry_base: str | None = None,
    skills_root: Path | str | None = None,
    plugin_root: Path | str | None = None,
    publisher_trust_store_path: Path | str | None = None,
) -> Any:
    require_fastapi(__name__)

    import os

    base = (
        registry_base or os.environ.get("ECHO_REGISTRY_URL") or "https://os.echo-age.com"
    ).rstrip("/")
    if skills_root is None:
        try:
            from runtime.platform.process.paths import resources_root

            skills_root = resources_root() / "skills" / "public"
        except Exception:  # noqa: BLE001
            skills_root = Path("skills/public")
    skills_root = Path(skills_root)
    if plugin_root is None:
        from runtime.platform.process.paths import app_paths

        plugin_root = app_paths().codex_plugins_path
    plugin_root = Path(plugin_root)
    publisher_trust_store_path = (
        Path(publisher_trust_store_path) if publisher_trust_store_path is not None else None
    )

    _branding_cache: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}
    _branding_key = f"{plugin_root}|{publisher_trust_store_path}"

    def _current_plugin_branding() -> dict[str, dict[str, Any]]:
        # 本地发现开销 ~0.4s/次(扫插件目录),加 5s TTL:安装新 bundle 后最迟 5s
        # 就能刷出新 logo,又避免每次读 registry 都重复扫盘。
        now = _time.monotonic()
        hit = _branding_cache.get(_branding_key)
        if hit is not None and (now - hit[0]) < 5.0:
            return hit[1]
        idx = _plugin_branding_index(plugin_root, publisher_trust_store_path)
        _branding_cache[_branding_key] = (now, idx)
        return idx

    def _auth_dep(request: Request) -> None:
        from runtime.adapters.web_auth import _resolve_actor

        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _admin_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_roles

        require_roles(
            request,
            identity_store,
            require_auth,
            ("admin",),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(tags=["registry"], dependencies=[Depends(_auth_dep)])

    @router.get("/api/registry/skills")
    def list_registry_skills(
        search: str | None = None,
        category: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
        refresh: bool = Query(default=False),
    ) -> dict[str, Any]:
        from echo_runtime import RegistryClient

        try:
            assets = _registry_list_cached(
                f"skills|{base}",
                lambda: RegistryClient(base).list_skills(),
                refresh=refresh,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"registry unreachable: {exc}") from exc
        if category:
            assets = [a for a in assets if (a.category or "") == category]
        if search:
            q = search.lower()
            assets = [
                a
                for a in assets
                if q in a.name.lower() or q in a.description.lower() or q in a.slug.lower()
            ]
        total = len(assets)
        paged = assets[offset : offset + limit]
        return {
            "skills": [a.model_dump() for a in paged],
            "total": total,
            "offset": offset,
            "limit": limit,
            "source": base,
        }

    @router.get("/api/registry/skills/{slug}")
    def registry_skill_detail(slug: str) -> dict[str, Any]:
        from echo_runtime import RegistryClient

        asset_id = slug if "/" in slug else f"skill/{slug}"
        try:
            p = RegistryClient(base).fetch(asset_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(404, f"skill not found: {slug} ({exc})") from exc
        d = p.model_dump()
        body = d.pop("body", "")
        d["body_preview"] = body[:1200]
        d["body_chars"] = len(body)
        d["installed"] = (skills_root / p.slug / "SKILL.md").is_file()
        return d

    @router.post(
        "/api/registry/skills/{slug}/install",
        dependencies=[Depends(_admin_dep)],
    )
    def install_registry_skill(slug: str) -> dict[str, Any]:
        from runtime.execution.suckers.market_skills import immutable_prompt_catalog_required

        if immutable_prompt_catalog_required():
            raise HTTPException(
                403,
                "remote prompt installation is disabled in shared/commercial deployments; "
                "ship prompt changes in a reviewed release artifact",
            )
        from echo_runtime import sync_skills

        try:
            ok, skipped, errors = sync_skills([slug], skills_root, base_url=base)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"install failed: {exc}") from exc
        if errors:
            raise HTTPException(502, f"install failed: {errors[0][1]}")
        if skipped and not ok:
            raise HTTPException(400, f"skipped: {skipped[0][1]}")
        registered = _register_runtime(skill_registry, skills_root)
        return {"installed": slug, "path": ok[0][1] if ok else None, "registered_now": registered}

    # ─── 角色(role / twin-role,数字分身岗位模板)─── 只读浏览 + 安装成本地 agent ───

    @router.get("/api/registry/roles")
    def list_registry_roles(
        search: str | None = None,
        category: str | None = None,
        role_type: str | None = Query(default=None, alias="type"),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
        refresh: bool = Query(default=False),
    ) -> dict[str, Any]:
        from echo_runtime import RegistryClient

        types = (role_type,) if role_type in _ROLE_ASSET_TYPES else _ROLE_ASSET_TYPES
        try:
            assets = _registry_list_cached(
                f"roles|{base}|{','.join(types)}",
                lambda: [a for t in types for a in RegistryClient(base).list_assets(type_=t)],
                refresh=refresh,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"registry unreachable: {exc}") from exc
        if category:
            assets = [a for a in assets if (a.category or "") == category]
        if search:
            q = search.lower()
            assets = [
                a
                for a in assets
                if q in a.name.lower() or q in a.description.lower() or q in a.slug.lower()
            ]
        total = len(assets)
        paged = assets[offset : offset + limit]
        return {
            "roles": [a.model_dump() for a in paged],
            "total": total,
            "offset": offset,
            "limit": limit,
            "source": base,
        }

    @router.get("/api/registry/roles/{asset_id:path}")
    def registry_role_detail(asset_id: str) -> dict[str, Any]:
        from echo_runtime import RegistryClient

        if "/" not in asset_id:
            asset_id = f"role/{asset_id}"
        try:
            p = RegistryClient(base).fetch(asset_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(404, f"role not found: {asset_id} ({exc})") from exc
        d = p.model_dump()
        body = d.pop("body", "")
        d["body_preview"] = body[:1200]
        d["body_chars"] = len(body)
        return d

    @router.post(
        "/api/registry/roles/{asset_id:path}/install",
        dependencies=[Depends(_admin_dep)],
    )
    def install_registry_role(asset_id: str) -> dict[str, Any]:
        from runtime.execution.suckers.market_skills import immutable_prompt_catalog_required

        if immutable_prompt_catalog_required():
            raise HTTPException(
                403,
                "remote role installation is disabled in shared/commercial deployments; "
                "ship role prompts in a reviewed release artifact",
            )
        from echo_runtime import RegistryClient

        if "/" not in asset_id:
            asset_id = f"role/{asset_id}"
        if _asset_type(asset_id) not in _ROLE_ASSET_TYPES:
            raise HTTPException(400, f"not a role asset: {asset_id}")
        try:
            asset = RegistryClient(base).fetch(asset_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(404, f"role not found: {asset_id} ({exc})") from exc
        if not _is_installable_role_asset(asset):
            raise HTTPException(
                400,
                f"not an installable role asset: type={asset.type or '?'} kind={asset.kind or '?'}",
            )
        agent_id, agent_root = _scaffold_local_agent_from_registry_asset(asset)
        return {
            "installed": True,
            "agent_id": agent_id,
            "name": asset.name,
            "path": str(agent_root),
        }

    # ─── 插件(plugin)─── 安装为 prompt-only 能力，不执行远程代码 ───

    @router.get("/api/registry/plugins")
    def list_registry_plugins(
        search: str | None = None,
        category: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
        refresh: bool = Query(default=False),
    ) -> dict[str, Any]:
        from echo_runtime import RegistryClient

        try:
            assets = _registry_list_cached(
                f"plugins|{base}",
                lambda: RegistryClient(base).list_assets(type_="plugin"),
                refresh=refresh,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"registry unreachable: {exc}") from exc
        if category:
            assets = [a for a in assets if (a.category or "") == category]
        if search:
            q = search.lower()
            assets = [
                a
                for a in assets
                if q in a.name.lower() or q in a.description.lower() or q in a.slug.lower()
            ]
        total = len(assets)
        paged = assets[offset : offset + limit]
        branding = _current_plugin_branding()
        plugin_rows = []
        for asset in paged:
            row = asset.model_dump()
            row = _enrich_registry_plugin_row(row, asset, branding)
            row["install_mode"] = (
                "plugin-bundle" if asset.bundle and asset.bundle.ref else "prompt-only"
            )
            row["installable"] = True
            plugin_rows.append(row)
        return {
            "plugins": plugin_rows,
            "total": total,
            "offset": offset,
            "limit": limit,
            "source": base,
            "installable": bool(paged),
            "install_mode": (
                "plugin-bundle"
                if any(asset.bundle and asset.bundle.ref for asset in paged)
                else "prompt-only"
            ),
        }

    @router.get("/api/registry/plugins/{slug}")
    def registry_plugin_detail(slug: str) -> dict[str, Any]:
        from echo_runtime import RegistryClient

        asset_id = slug if "/" in slug else f"plugin/{slug}"
        try:
            p = RegistryClient(base).fetch(asset_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(404, f"plugin not found: {slug} ({exc})") from exc
        d = p.model_dump()
        d = _enrich_registry_plugin_row(d, p, _current_plugin_branding())
        body = d.pop("body", "")
        d["body_preview"] = body[:1200]
        d["body_chars"] = len(body)
        d["installable"] = True
        d["install_mode"] = "plugin-bundle" if p.bundle and p.bundle.ref else "prompt-only"
        return d

    @router.post(
        "/api/registry/plugins/{slug}/install",
        dependencies=[Depends(_admin_dep)],
    )
    def install_registry_plugin(slug: str) -> dict[str, Any]:
        from echo_runtime import RegistryClient

        asset_id = slug if "/" in slug else f"plugin/{slug}"
        if _asset_type(asset_id) != "plugin":
            raise HTTPException(400, f"not a plugin asset: {asset_id}")
        try:
            client = RegistryClient(base)
            asset = client.fetch(asset_id)
            if asset.bundle and asset.bundle.ref:
                result = _install_registry_plugin_bundle(
                    asset,
                    client=client,
                    plugin_root=plugin_root,
                    publisher_trust_store_path=publisher_trust_store_path,
                )
                return {"installed": asset_id, "install_mode": "plugin-bundle", **result}
            from runtime.execution.suckers.market_skills import (
                immutable_prompt_catalog_required,
            )

            if immutable_prompt_catalog_required():
                raise HTTPException(
                    403,
                    "unsigned remote plugin prompts are disabled in shared/commercial "
                    "deployments; install a trusted signed plugin bundle instead",
                )
            installed_name, path = _materialize_registry_plugin_prompt(asset, skills_root)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - normalize registry/install failures
            raise HTTPException(502, f"install failed: {exc}") from exc
        registered = _register_runtime(skill_registry, skills_root)
        return {
            "installed": asset_id,
            "installed_name": installed_name,
            "path": str(path),
            "registered_now": registered,
            "install_mode": "prompt-only",
        }

    return router
