#!/usr/bin/env python3
"""把本地技能/插件打包成云端内容包(从云端安装下载用)。

产出(remote/bundles/):
  echo-skills.tar.gz  内含 skills/<name>/SKILL.md + scripts/references/meta.json
  echo-plugins.tar.gz 内含 plugins/<id>/(codex 插件 plugin.json+skills / 连接器 cli.json+mcp.json+skills)

用法:
  python3 extensions/workbuddy-experts/scripts/build-cloud-bundles.py
  python3 extensions/workbuddy-experts/scripts/build-cloud-bundles.py --out remote/bundles
"""

import argparse
import base64
import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from runtime.platform.plugins.marketplace_package import (
    CODEX_SIGNATURE_RELATIVE_PATH,
    CONNECTOR_MANIFEST_RELATIVE_PATH,
    CONNECTOR_MANIFEST_SCHEMA,
    CONNECTOR_RELEASE_SUMMARY,
    CONNECTOR_SIGNATURE_RELATIVE_PATH,
    compute_marketplace_content_provenance,
    derive_codex_package_requirements,
    derive_connector_package_requirements,
    load_marketplace_package_manifest,
)
from runtime.platform.plugins.publisher_provenance import (
    canonical_publisher_signature_payload,
)
from runtime.platform.plugins.workbench_package import (
    WORKBENCH_SIGNATURE_RELATIVE_PATH,
    WorkbenchPackageStore,
    compute_workbench_content_provenance,
)

REPO = Path(__file__).resolve().parents[3]
STORE_DATA = REPO / "extensions" / "workbuddy-experts" / "storefront" / "data"
BUILTIN_SKILLS = REPO / "runtime" / "execution" / "all_skills"
USER_SKILLS = Path.home() / ".echo" / "skills"
REPOSITORY_SKILL_TREES = (
    REPO / "extensions" / "workbuddy-experts" / "builtin",
    REPO / "agents",
)
# Codex 格式插件统一放 echo 名下;旧 ~/.codex/plugins/cache 由同步一次性搬入。
CODEX_CACHE = Path.home() / ".echo" / "plugins" / "codex"
REPO_CODEX_PLUGINS = REPO / "extensions" / "codex-plugins"
REPO_ECHO_PLUGINS = REPO / ".echo" / "plugins" / "codex"
CONNECTOR_ROOT = REPO / "extensions" / "workbuddy-connectors" / "connectors"
CONNECTOR_CATALOG = (
    REPO
    / "extensions"
    / "workbuddy-connectors"
    / ".codebuddy-connector"
    / "connectors.json"
)
WORKBENCH_ROOT = REPO / "extensions" / "workbench-apps"

# codex 插件根目录里跳过的大文件目录(运行时/构建产物,本地安装本就不需要)
_CODEX_SKIP_DIRS = {"node_modules", "dist", "build", ".git", "__pycache__"}
_WORKBENCH_SKIP_DIRS = {"node_modules", "build", ".git", "__pycache__"}
NARRATIVE_BACKEND = REPO / "runtime" / "platform" / "plugins" / "bundled" / "narrative_studio"
PAPER_TRADING_BACKEND = REPO / "runtime" / "platform" / "plugins" / "bundled" / "paper_trading"
REMOTE_WORKBENCH_BACKENDS = {
    "narrative_studio": NARRATIVE_BACKEND,
    "paper-trading": PAPER_TRADING_BACKEND,
}


def _sign_package(
    package: Path,
    *,
    manifest: dict[str, str],
    signature_relative_path: Path,
    provenance: dict,
) -> bool:
    """Attach one release Ed25519 envelope when CI supplied a signing key."""

    encoded_key = os.environ.get("ECHO_PLUGIN_SIGNING_PRIVATE_KEY", "").strip()
    if not encoded_key:
        return False
    publisher_id = os.environ.get("ECHO_PLUGIN_SIGNING_PUBLISHER_ID", "echoai").strip()
    key_id = os.environ.get("ECHO_PLUGIN_SIGNING_KEY_ID", "").strip()
    if not publisher_id or not key_id:
        raise RuntimeError(
            "ECHO_PLUGIN_SIGNING_PUBLISHER_ID and ECHO_PLUGIN_SIGNING_KEY_ID "
            "are required when signing content"
        )
    try:
        private_bytes = base64.b64decode(encoded_key, validate=True)
        private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            "ECHO_PLUGIN_SIGNING_PRIVATE_KEY must be a base64 Ed25519 private key"
        ) from exc
    if provenance.get("complete") is not True:
        raise RuntimeError(f"cannot sign incomplete package: {package.name}")
    payload = canonical_publisher_signature_payload(
        plugin_id=manifest["name"],
        version=manifest["version"],
        content_digest=str(provenance["digest"]),
        publisher_id=publisher_id,
        key_id=key_id,
    )
    envelope = {
        "schema": "echo.plugin_publisher_signature.v1",
        "algorithm": "ed25519",
        "plugin_id": manifest["name"],
        "version": manifest["version"],
        "content_digest": provenance["digest"],
        "publisher_id": publisher_id,
        "key_id": key_id,
        "signature": base64.b64encode(private_key.sign(payload)).decode("ascii"),
    }
    signature_path = package / signature_relative_path
    signature_path.parent.mkdir(parents=True, exist_ok=True)
    signature_path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return True


def _sign_workbench(package: Path) -> bool:
    manifest = WorkbenchPackageStore(package.parent).load_manifest(package.name)
    return _sign_package(
        package,
        manifest={"name": manifest.id, "version": manifest.version},
        signature_relative_path=WORKBENCH_SIGNATURE_RELATIVE_PATH,
        provenance=compute_workbench_content_provenance(package),
    )


def _sign_marketplace_package(package: Path, *, package_kind: str) -> bool:
    signature_path = (
        CODEX_SIGNATURE_RELATIVE_PATH
        if package_kind == "codex"
        else CONNECTOR_SIGNATURE_RELATIVE_PATH
    )
    return _sign_package(
        package,
        manifest=load_marketplace_package_manifest(package, package_kind=package_kind),
        signature_relative_path=signature_path,
        provenance=compute_marketplace_content_provenance(
            package,
            signature_relative_path=signature_path,
        ),
    )


def _tar_add(
    tf: tarfile.TarFile,
    src: Path,
    arc_prefix: str,
    *,
    skip_dirs: set[str] | None = None,
) -> int:
    """把 src 目录递归加进 tar,返回文件数。"""
    n = 0
    if not src.exists():
        return n
    skipped = _CODEX_SKIP_DIRS if skip_dirs is None else skip_dirs
    for p in sorted(src.rglob("*")):
        if any(part in skipped for part in p.relative_to(src).parts):
            continue
        if p.is_file():
            tf.add(p, arcname=f"{arc_prefix}/{p.relative_to(src)}", recursive=False)
            n += 1
    return n


def _write_connector_archive(out: Path, package: Path, connector_id: str) -> Path:
    """Write one independently downloadable, signed connector package."""

    safe = re.sub(r"[^A-Za-z0-9_-]", "_", connector_id).strip("_")
    if not safe or safe != connector_id:
        raise RuntimeError(f"unsafe connector package id: {connector_id!r}")
    archive = out / f"echo-connector-{safe}.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        _tar_add(tf, package, f"plugins/connector/{safe}")
    return archive


def build_skills(out: Path) -> int:
    """Pack exactly the skills advertised by the generated release index."""

    catalog_path = STORE_DATA / "skill-registry.json"
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("skill registry must be generated before the content pack") from exc
    rows = catalog.get("skills")
    if not isinstance(rows, list):
        raise RuntimeError("skill registry is invalid")
    source_dirs: list[Path] = [
        directory
        for directory in sorted(BUILTIN_SKILLS.iterdir())
        if directory.is_dir() and (directory / "SKILL.md").is_file()
    ]
    for tree in REPOSITORY_SKILL_TREES:
        if tree.is_dir():
            source_dirs.extend(
                skill_md.parent
                for skill_md in sorted(tree.rglob("SKILL.md"))
                if not any(
                    part in {"node_modules", "release", "build", ".git", "__pycache__"}
                    for part in skill_md.parent.relative_to(tree).parts
                )
            )
    if (
        os.environ.get("CI", "").strip().lower() not in {"1", "true", "yes"}
        and USER_SKILLS.is_dir()
    ):
        source_dirs.extend(
            directory
            for directory in sorted(USER_SKILLS.iterdir())
            if directory.is_dir() and (directory / "SKILL.md").is_file()
        )

    def declared_name(directory: Path) -> str:
        text = (directory / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
        if match:
            for line in match.group(1).splitlines():
                if ":" in line and line.split(":", 1)[0].strip().lower() == "name":
                    return line.split(":", 1)[1].strip().strip("\"'") or directory.name
        return directory.name

    sources: dict[str, Path] = {}
    for directory in source_dirs:
        sources.setdefault(declared_name(directory), directory)
    count = 0
    with tarfile.open(out / "echo-skills.tar.gz", "w:gz") as tf:
        seen: set[str] = set()
        for row in rows:
            name = str(row.get("name") or "") if isinstance(row, dict) else ""
            if not name or name in seen:
                raise RuntimeError("skill registry contains a missing or duplicate name")
            root = sources.get(name)
            if root is None:
                raise RuntimeError(f"skill registry source is unavailable: {name}")
            seen.add(name)
            count += _tar_add(tf, root, f"skills/{name}")
    return count


def build_plugins(out: Path) -> int:
    """打包 Codex 插件、连接器和按需工作台到统一内容包。"""
    count = 0
    connector_rows: dict[str, dict] = {}
    if CONNECTOR_CATALOG.is_file():
        catalog = json.loads(CONNECTOR_CATALOG.read_text(encoding="utf-8"))
        connector_rows = {
            str(row.get("id") or ""): row
            for row in catalog.get("connectors", [])
            if isinstance(row, dict) and row.get("id")
        }
    with tarfile.open(out / "echo-plugins.tar.gz", "w:gz") as tf:
        # codex 格式插件:~/.echo/plugins/codex/<plugin>(旧缓存首次自动同步)
        if not CODEX_CACHE.is_dir():
            from runtime.platform.plugins.codex_discovery import (
                sync_codex_cache_to_echo,
            )

            sync_codex_cache_to_echo(dest=CODEX_CACHE)
        with tempfile.TemporaryDirectory(prefix="echo-marketplace-pack-") as tmp:
            stage_root = Path(tmp)
            seen: set[str] = set()
            source_roots = [REPO_CODEX_PLUGINS, REPO_ECHO_PLUGINS]
            if os.environ.get("CI", "").strip().lower() not in {"1", "true", "yes"}:
                source_roots.append(CODEX_CACHE)
            for source_root in source_roots:
                if not source_root.is_dir():
                    continue
                for manifest_path in sorted(source_root.glob("*/.codex-plugin/plugin.json")):
                    try:
                        meta = json.loads(manifest_path.read_text("utf-8"))
                        pid = str(meta.get("name") or "")
                    except (OSError, json.JSONDecodeError):
                        continue
                    if not pid or pid in seen:
                        continue
                    seen.add(pid)
                    stage = stage_root / "codex" / pid
                    shutil.copytree(
                        manifest_path.parent.parent,
                        stage,
                        ignore=shutil.ignore_patterns(*_CODEX_SKIP_DIRS),
                    )
                    staged_manifest = stage / ".codex-plugin" / "plugin.json"
                    staged_meta = json.loads(staged_manifest.read_text(encoding="utf-8"))
                    if not any(
                        staged_meta.get(field)
                        for field in ("releaseNotes", "release_notes", "release_summary")
                    ):
                        staged_meta["releaseNotes"] = (
                            f"{staged_meta.get('version')}：由 Echo 受信发布链重新封装当前插件内容。"
                        )
                    staged_meta["echo"] = derive_codex_package_requirements(
                        staged_meta,
                        package_dir=stage,
                    )
                    staged_manifest.write_text(
                        json.dumps(
                            staged_meta,
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    _sign_marketplace_package(stage, package_kind="codex")
                    count += _tar_add(tf, stage, f"plugins/codex/{pid}")
            # 连接器:extensions/workbuddy-connectors/connectors/<id>
            if CONNECTOR_ROOT.exists():
                for source in sorted(CONNECTOR_ROOT.iterdir()):
                    if not source.is_dir() or source.name.startswith((".", "__")):
                        continue
                    stage = stage_root / "connector" / source.name
                    shutil.copytree(
                        source,
                        stage,
                        ignore=shutil.ignore_patterns(*_CODEX_SKIP_DIRS),
                    )
                    connector_manifest = stage / CONNECTOR_MANIFEST_RELATIVE_PATH
                    connector_manifest.parent.mkdir(parents=True, exist_ok=True)
                    requirements = derive_connector_package_requirements(
                        connector_rows.get(source.name, {}),
                        package_dir=stage,
                    )
                    version = str(
                        (connector_rows.get(source.name) or {}).get("version") or "1.0.0"
                    )
                    release_summary = (
                        CONNECTOR_RELEASE_SUMMARY
                        if version == "1.0.0"
                        else f"{version}：纳入 Echo 受信连接器内容包。"
                    )
                    connector_manifest.write_text(
                        json.dumps(
                            {
                                "schema": CONNECTOR_MANIFEST_SCHEMA,
                                "id": source.name,
                                "version": version,
                                "release_summary": release_summary,
                                **requirements,
                            },
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    _sign_marketplace_package(stage, package_kind="connector")
                    count += _tar_add(tf, stage, f"plugins/connector/{source.name}")
                    _write_connector_archive(out, stage, source.name)
        # 工作台应用:轻量清单先随包交付;后续构建产物也放在同一目录,
        # 无需改变安装协议或客户端目录结构。
        if WORKBENCH_ROOT.exists():
            with tempfile.TemporaryDirectory(prefix="echo-workbench-pack-") as tmp:
                stage_root = Path(tmp)
                for d in sorted(WORKBENCH_ROOT.iterdir()):
                    if not d.is_dir() or not (d / "app.json").is_file():
                        continue
                    if not (d / "dist" / "index.html").is_file():
                        raise RuntimeError(
                            f"workbench build is missing for {d.name}; "
                            "run `pnpm --dir frontend build:workbenches` first"
                        )
                    stage = stage_root / d.name
                    backend = REMOTE_WORKBENCH_BACKENDS.get(d.name)
                    if backend is not None:
                        shutil.copytree(
                            backend,
                            stage,
                            ignore=shutil.ignore_patterns(*_WORKBENCH_SKIP_DIRS, "*.pyc", "*.pyo"),
                        )
                        shutil.copytree(d, stage, dirs_exist_ok=True)
                    else:
                        shutil.copytree(d, stage)
                    _sign_workbench(stage)
                    count += _tar_add(
                        tf,
                        stage,
                        f"plugins/workbench/{d.name}",
                        skip_dirs=_WORKBENCH_SKIP_DIRS,
                    )
    return count


def main() -> None:
    ap = argparse.ArgumentParser(description="打包本地技能/插件为云端内容包")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出目录(默认 extensions/workbuddy-experts/remote/bundles)",
    )
    ap.add_argument(
        "--kind",
        choices=("all", "plugins", "skills"),
        default="all",
        help="只构建指定内容包(默认 all)",
    )
    args = ap.parse_args()
    out = args.out or REPO / "extensions" / "workbuddy-experts" / "remote" / "bundles"
    out.mkdir(parents=True, exist_ok=True)

    n_skills = build_skills(out) if args.kind in {"all", "skills"} else 0
    n_plugins = build_plugins(out) if args.kind in {"all", "plugins"} else 0
    files = []
    if args.kind in {"all", "skills"}:
        files.append("echo-skills.tar.gz")
    if args.kind in {"all", "plugins"}:
        files.append("echo-plugins.tar.gz")
    for f in files:
        p = out / f
        print(f"✔ {p} — {p.stat().st_size / 1024:.1f} KB")
    print(f"  技能文件数: {n_skills} | 插件/连接器文件数: {n_plugins}")


if __name__ == "__main__":
    main()
