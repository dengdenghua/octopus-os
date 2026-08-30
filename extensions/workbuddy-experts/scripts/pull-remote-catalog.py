#!/usr/bin/env python3
"""Pull the full WorkBuddy Expert Center catalog (lightweight).

Downloads:
  1. The authoritative manifest expert_center.json (421 experts / 15 categories)
  2. Every expert's lead agent markdown (promptFile) → remote/agents/<plugin>/
  3. Builds remote/INDEX.md + merges into a unified index

Full bundles (all team members / skills / avatars / references) are ~1GB+ and
are NOT pulled by default; use `--bundles` to fetch them (heavy).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://acc-1258344699.cos.accelerate.myqcloud.com/workbuddy/expert-marketplace"
MANIFEST_URL = f"{BASE}/expert_center.json"
FORK_ROOT = Path(__file__).resolve().parent.parent
REMOTE = FORK_ROOT / "remote"
AGENTS_DIR = REMOTE / "agents"


def _get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "echo-workbuddy-fork"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def fetch_manifest() -> dict:
    return json.loads(_get(MANIFEST_URL))


def pull_one(expert: dict) -> tuple[str, str]:
    plugin = str(expert.get("plugin") or expert["id"])
    pf = expert.get("promptFile")
    if not pf:
        return plugin, "no-prompt-file"
    rel = pf.lstrip("/")
    name = Path(rel).name
    dest = AGENTS_DIR / plugin / name
    if dest.exists():
        return plugin, "exists"
    try:
        data = _get(f"{BASE}/{rel}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return plugin, f"ok({len(data)}B)"
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
        return plugin, f"err:{e}"




def _safe_extract(tf: "tarfile.TarFile", dest: Path) -> None:
    """Path-traversal-safe extraction that works on Python 3.9+."""
    dest_res = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        if dest_res not in target.parents and target != dest_res:
            raise ValueError(f"unsafe tar path: {member.name}")
    tf.extractall(dest)


def pull_bundles(manifest: dict, args) -> tuple[int, int]:
    """Download + extract every expert bundle. Returns (done, failed)."""
    import io
    import tarfile

    bundles_dir = REMOTE / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    experts = manifest.get("experts", [])

    def one(expert: dict) -> tuple[str, str]:
        plugin = str(expert.get("plugin") or expert["id"])
        dest = bundles_dir / plugin
        if dest.exists() and any(dest.iterdir()):
            return plugin, "exists"
        url = f"{BASE}/bundles/{plugin}.tar.gz"
        try:
            raw = _get(url, timeout=120)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            return plugin, f"err:{e}"
        dest.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
                _safe_extract(tf, dest)
        except Exception as e:  # noqa: BLE001
            return plugin, f"extract-err:{e}"
        if args.strip_avatars:
            for av in dest.rglob("avatars"):
                if av.is_dir():
                    for img in av.rglob("*.png"):
                        img.unlink(missing_ok=True)
                    for img in av.rglob("*.PNG"):
                        img.unlink(missing_ok=True)
        return plugin, "ok"

    done = failed = 0
    with cf.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for plugin, status in pool.map(one, experts):
            if status.startswith("ok") or status == "exists":
                done += 1
            else:
                failed += 1
                print(f"  ! {plugin}: {status}")
    return done, failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--no-index", action="store_true")
    ap.add_argument("--bundles", action="store_true", help="download full .tar.gz bundles (heavy, ~1GB)")
    ap.add_argument("--strip-avatars", action="store_true", help="with --bundles: delete avatars/*.png after extract (~80% size)")
    args = ap.parse_args()

    print("fetching manifest…")
    REMOTE.mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = fetch_manifest()
    (REMOTE / "expert_center.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    experts = manifest.get("experts", [])
    print(f"manifest: {len(experts)} experts")

    results: dict[str, str] = {}
    with cf.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for plugin, status in pool.map(pull_one, experts):
            results[plugin] = status
    ok = sum(1 for s in results.values() if s.startswith("ok") or s == "exists")
    err = sum(1 for s in results.values() if s.startswith("err"))
    print(f"pulled {ok} agent mds, {err} errors")

    if args.bundles:
        done, failed = pull_bundles(manifest, args)
        print(f"bundles: {done} ok, {failed} failed ({'avatars stripped' if args.strip_avatars else 'avatars kept'})")

    if args.no_index:
        return 0

    # INDEX.md
    by_cat: dict[str, list[dict]] = {}
    for e in experts:
        by_cat.setdefault(e.get("categoryId", "?"), []).append(e)
    lines = [
        "# WorkBuddy Expert Center — 全量目录(远程镜像)",
        "",
        f"> 来源: `{BASE}` · 共 **{len(experts)}** 个专家 "
        f"(agent {sum(1 for e in experts if e.get('expertType')=='agent')} / "
        f"team {sum(1 for e in experts if e.get('expertType')=='team')})",
        "",
        "## 分类统计",
        "",
        "| categoryId | 数量 |",
        "|---|---|",
    ]
    for cid, items in sorted(by_cat.items()):
        lines.append(f"| {cid} | {len(items)} |")
    lines.append("")
    lines.append("## 全量列表")
    lines.append("")
    for e in sorted(experts, key=lambda x: x.get("id", "")):
        dn = e.get("displayName", {})
        lines.append(
            f"- **{e.get('id')}** [{e.get('expertType')}] · "
            f"{dn.get('zh') if isinstance(dn, dict) else dn} — {e.get('categoryId')}"
        )
    (REMOTE / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"INDEX.md 写入 {REMOTE / 'INDEX.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
