"""echo-runtime CLI:`python -m echo_runtime <list|sync> ...`。

  list                                列 registry 技能(id / kind / platforms / name)
  sync <slug>... [--skills-dir DIR]   拉取 + 校验 + 落地到 <DIR>/<slug>/SKILL.md(默认 ./skills/public)

产品在启动前/按需打这一发即可把云端技能落到本地现有布局,之后自有 loader 接管。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .bootstrap import bootstrap_skills, write_lockfile
from .client import DEFAULT_BASE, RegistryClient
from .materialize import sync_skills


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="echo_runtime", description="echo-runtime 资产消费端(registry → 落地)")
    ap.add_argument("command", choices=["list", "sync", "bootstrap", "lockfile"])
    ap.add_argument("slugs", nargs="*", help="sync: 技能 slug 列表")
    ap.add_argument("--skills-dir", default="skills/public", help="落地目录(默认 ./skills/public)")
    ap.add_argument("--lockfile", default="skills.lock.json", help="bootstrap/lockfile: lockfile 路径")
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"registry 基址(默认 {DEFAULT_BASE})")
    ap.add_argument("--mobile", action="store_true", help="list: 只显示适合手机(platforms 含 mobile)的")
    ap.add_argument("--allow-code", action="store_true", help="sync: 允许落地 code-kind(默认只 data-kind)")
    ap.add_argument("--force", action="store_true", help="bootstrap: 已存在也重新拉")
    a = ap.parse_args(argv)

    if a.command == "lockfile":
        slugs = write_lockfile(a.skills_dir, a.lockfile)
        print(f"已生成 lockfile {a.lockfile}({len(slugs)} 个技能)")
        return 0

    if a.command == "bootstrap":
        synced, present, errors = bootstrap_skills(a.lockfile, a.skills_dir, base_url=a.base, force=a.force)
        print(f"bootstrap:同步 {len(synced)} · 已有 {len(present)} · 失败 {len(errors)}(lockfile={a.lockfile})")
        for slug, why in errors:
            print(f"✗ {slug}:{why}", file=sys.stderr)
        return 1 if errors else 0

    if a.command == "list":
        rows = RegistryClient(a.base).list_skills()
        if a.mobile:
            rows = [s for s in rows if (s.platforms is None) or ("mobile" in s.platforms)]
        for s in rows:
            plats = ",".join(s.platforms) if s.platforms else "?"
            print(f"{s.id:42} {s.kind:5} [{plats:13}] {s.name}")
        print(f"\n共 {len(rows)} 个")
        return 0

    if not a.slugs:
        ap.error("sync 需要至少一个 slug(如:python -m echo_runtime sync brainstorming)")
    ok, skipped, errors = sync_skills(a.slugs, Path(a.skills_dir), base_url=a.base, allow_code=a.allow_code)
    for slug, info in ok:
        print(f"✓ {slug} → {info}")
    for slug, why in skipped:
        print(f"– {slug} 跳过:{why}")
    for slug, why in errors:
        print(f"✗ {slug} 失败:{why}", file=sys.stderr)
    print(f"\n落地 {len(ok)} · 跳过 {len(skipped)} · 失败 {len(errors)}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
