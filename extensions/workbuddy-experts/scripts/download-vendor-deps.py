#!/usr/bin/env python3
"""批量下载连接器依赖到 vendor/(让商城包离线可装)。

对 extensions/workbuddy-connectors/connectors/<id>/:
- cli.json 的 init 命令里 `npm (i|install) -g <pkg>` → npm pack <pkg> 到 vendor/
- mcp.json 里 server 是本地 command(如 `npx -y <pkg>`) → 提取包 → npm pack 到 vendor/
- 已存在 vendor/*.tgz 的连接器自动跳过(幂等)
- URL / pip / curl 形式的安装命令 → 记录为不支持原因(不误伤)

用法:
  python3 extensions/workbuddy-experts/scripts/download-vendor-deps.py --dry-run   # 只看分类
  python3 extensions/workbuddy-experts/scripts/download-vendor-deps.py            # 实际下载
  python3 extensions/workbuddy-experts/scripts/download-vendor-deps.py --only cnb-api
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CONNECTOR_ROOT = REPO / "extensions" / "workbuddy-connectors" / "connectors"

# npm 包名(含 scope 与可选 @version)
_PKG_RE = re.compile(r"(@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*" r"(@[\w.\-]+)?", re.IGNORECASE)


def extract_npm_pkg(install_cmd: str) -> str | None:
    """从 `npm i -g <pkg>` / `npx -y <pkg> ...` 提取包名(含 scope/version)。"""
    m = re.search(
        r"(?:npm\s+(?:i|install)\s+-g\s+|npx(?:\s+-y)?\s+)"
        r"((?:@[a-z0-9][\w.-]*\/)?[a-z0-9][\w.-]*(?:@[\w.\-]+)?)",
        install_cmd,
        re.IGNORECASE,
    )
    return m.group(1) if m else None


def classify(connector_id: str) -> tuple[str, str | None]:
    """返回 (类型, npm包名或 None)。类型: npm | url | pip | curl | skip | none"""
    root = CONNECTOR_ROOT / connector_id
    # 已有 vendor 则跳过
    vdir = root / "vendor"
    if vdir.is_dir() and list(vdir.glob("*.tgz")):
        return "done", None
    # 1) cli.json
    cli = root / "cli.json"
    if cli.exists():
        try:
            data = json.loads(cli.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        init = data.get("init") or {}
        cmd = (init.get("darwin") or init.get("linux") or "").strip()
        if cmd.startswith("npm"):
            if "://" in cmd and "registry" not in cmd:
                return "url", None  # npm install -g https://... 是 URL 安装
            pkg = extract_npm_pkg(cmd)
            if pkg:
                return "npm", pkg
        elif cmd.startswith("python") or "pip" in cmd:
            return "pip", None
        elif cmd.startswith("curl") or cmd.startswith("http"):
            return "url", None
        elif cmd.startswith("node") or "CONNECTOR_HOME" in cmd:
            return "skip", None
    # 2) mcp.json 本地 command
    mcp = root / "mcp.json"
    if mcp.exists():
        try:
            data = json.loads(mcp.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        servers = data.get("servers") or data.get("mcpServers") or {}
        for cfg in servers.values():
            if isinstance(cfg, dict) and cfg.get("command"):
                cmd = f"{cfg['command']} {' '.join(cfg.get('args', []))}"
                pkg = extract_npm_pkg(cmd)
                if pkg:
                    return "npm", pkg
                return "skip", None
    # 3) 什么都没匹配
    has_files = any(p.is_file() for p in root.rglob("*") if p.name != "SKILL.md")
    return ("none" if not has_files else "skip", None)


def npm_pack(connector_id: str, pkg: str, dry_run: bool) -> tuple[bool, str]:
    """npm pack <pkg> 到 connector 的 vendor/。"""
    vdir = CONNECTOR_ROOT / connector_id / "vendor"
    if dry_run:
        return True, f"[dry-run] npm pack {pkg}"
    vdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = subprocess.run(
                ["npm", "pack", pkg, "--pack-destination", tmp],
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            return False, f"npm pack 超时: {pkg}"
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout or "").strip()[:200]
        # 把下载的 tgz 拷进 vendor
        for f in Path(tmp).glob("*.tgz"):
            shutil.copy2(f, vdir / f.name)
        names = [f.name for f in vdir.glob("*.tgz")]
        return True, f"npm pack {pkg} -> {'/'.join(names)}"


def main() -> None:
    ap = argparse.ArgumentParser(description="批量下载连接器依赖到 vendor/")
    ap.add_argument("--dry-run", action="store_true", help="只分类不下载")
    ap.add_argument("--only", help="只处理指定 connector id")
    args = ap.parse_args()

    ids = sorted(
        d.name
        for d in CONNECTOR_ROOT.iterdir()
        if d.is_dir() and not d.name.startswith((".", "__"))
    )
    if args.only:
        ids = [i for i in ids if i == args.only]

    stats: dict[str, int] = {}
    for cid in ids:
        kind, pkg = classify(cid)
        stats[kind] = stats.get(kind, 0) + 1
        if kind == "npm":
            ok, msg = npm_pack(cid, pkg, args.dry_run)
            flag = "✔" if ok else "✘"
            print(f"{flag} {cid:22s} npm {pkg:34s} {msg[:80]}")
        elif args.dry_run:
            print(f"· {cid:22s} {kind}")
        else:
            print(f"· {cid:22s} {kind} (未 vendor)")

    print("\n分类统计:", json.dumps(stats, ensure_ascii=False))
    print("说明: npm=已下载到 vendor | pip/url/curl/skip=需人工 | done=已有 vendor")


if __name__ == "__main__":
    main()
