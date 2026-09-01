#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish-cloud.py — 把 WorkBuddy 专家商城发布到云端(GitHub Pages + Release 资产)

让 421 位专家做成"我们自己的商城":
  storefront/(纯静态商城)  -> GitHub Pages,任何人都能浏览/搜索/下载
  remote/bundles/*.tar.gz  -> (可选)GitHub Release 资产,彻底脱离腾讯 COS

依赖: gh CLI 已登录(gh auth status),git 可用。

用法:
  # 1) 只发布商城页面(默认):推到 workbuddy-expert-market 的 gh-pages
  python3 scripts/publish-cloud.py

  # 2) 指定目标仓库
  python3 scripts/publish-cloud.py --repo dengdenghua/workbuddy-expert-market

  # 3) 发布商城 + 把本地 bundles 传成 GitHub Release 资产(默认关闭,体积大)
  python3 scripts/publish-cloud.py --upload-bundles

  # 4) 保留 COS 直链(默认):数据里 bundleUrl 仍指向腾讯 COS
  python3 scripts/publish-cloud.py --keep-cos

说明:
  - 商城数据 expert-store.json 默认 bundleUrl 指向腾讯 COS(加速节点,免流量)。
    想完全自托管时用 --upload-bundles 上传到 GitHub Release 并重写 URL。
  - 新仓库不存在会自动创建(公开,供 Pages 访问)。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # extensions/workbuddy-experts
STOREFRONT = ROOT / "storefront"
DATA = STOREFRONT / "data" / "expert-store.json"
BUNDLES = ROOT / "remote" / "bundles"

DEFAULT_REPO = "dengdenghua/workbuddy-expert-market"
GH_PAGES_BRANCH = "gh-pages"


def sh(cmd, **kw):
    """运行命令,出错即抛。"""
    print("$", " ".join(cmd) if isinstance(cmd, list) else cmd)
    r = subprocess.run(cmd, text=True, capture_output=True, **kw)
    if r.returncode != 0:
        raise SystemExit(f"命令失败({r.returncode}): {cmd}\n{r.stderr or r.stdout}")
    return r.stdout.strip()


def gh(cmd, err_ok=False, **kw):
    if err_ok:
        r = subprocess.run(["gh"] + cmd, text=True, capture_output=True, **kw)
        if r.returncode != 0:
            return r.stdout.strip() or r.stderr.strip()
        return r.stdout.strip()
    return sh(["gh"] + cmd, **kw)


def check_gh():
    gh(["auth", "status"])


def repo_exists(repo):
    r = subprocess.run(["gh", "repo", "view", repo, "--json", "name"],
                       text=True, capture_output=True)
    return r.returncode == 0


def ensure_repo(repo):
    if repo_exists(repo):
        print(f"✔ 仓库已存在: {repo}")
        return
    print(f"创建公开仓库 {repo} …")
    gh(["repo", "create", repo, "--public", "--description",
        "🐙 Echo 专家商城 — WorkBuddy(腾讯)领域专家/专家团 全量镜像(421 位)"])


def publish_storefront(repo):
    """把 storefront/ 推到 gh-pages 分支并开启 GitHub Pages。"""
    print("\n=== 1/3 发布商城页面到 GitHub Pages ===")
    ensure_repo(repo)
    owner, name = repo.split("/")

    # 用一个临时 git 仓库,只含 storefront 内容
    with tempfile.TemporaryDirectory(prefix="store-pub-") as tmp:
        tmpd = Path(tmp)
        # 把商城内容放到 git 仓库根(而非 site/ 子目录),否则 gh-pages 会 404
        shutil.copytree(STOREFRONT, tmpd, dirs_exist_ok=True)
        # 发布站点不携带本地 COS 备份文件
        backup_out = tmpd / "data" / "expert-store.cos.json"
        if backup_out.exists():
            backup_out.unlink()
        with open(tmpd / ".nojekyll", "w") as f:
            f.write("")

        sh(["git", "init", "-b", GH_PAGES_BRANCH], cwd=tmpd)
        sh(["git", "config", "user.email", "echo-bot@users.noreply.github.com"], cwd=tmpd)
        sh(["git", "config", "user.name", "echo-bot"], cwd=tmpd)
        sh(["git", "add", "-A"], cwd=tmpd)
        # 强制推到 gh-pages(允许覆盖旧内容)
        remote = f"https://github.com/{repo}.git"
        sh(["git", "commit", "-m", f"deploy: WorkBuddy 专家商城 gh-pages ({GH_PAGES_BRANCH})"], cwd=tmpd)
        sh(["git", "push", "-f", remote, f"HEAD:{GH_PAGES_BRANCH}"], cwd=tmpd)

    # 开启 GitHub Pages(Source: gh-pages branch)
    try:
        gh(["api", f"repos/{repo}/pages",
            "-X", "POST", "-f", f"source[branch]={GH_PAGES_BRANCH}",
            "-f", "source[path]=/"], err_ok=True)
    except SystemExit:
        # 已开启则 PUT 更新
        gh(["api", f"repos/{repo}/pages", "-X", "PUT",
            "-f", f"source[branch]={GH_PAGES_BRANCH}", "-f", "source[path]=/"])
    print(f"✔ 商城已发布: https://{owner}.github.io/{name}/")
    return f"https://{owner}.github.io/{name}/"


def rewrite_bundle_url(data, mapping):
    """把 expert-store.json 的 bundleUrl 按 mapping 重写。mapping: {plugin: url}"""
    n = 0
    for e in data["experts"]:
        plugin = e["plugin"]
        if plugin in mapping:
            e["bundleUrl"] = mapping[plugin]
            n += 1
    return n


def upload_bundles(repo, bundles_dir):
    """把 remote/bundles/*.tar.gz 上传为 GitHub Release 资产,返回 {plugin: url}。"""
    print("\n=== 2/3 上传 bundles 到 GitHub Release(自托管,可能较慢) ===")
    tarballs = sorted(bundles_dir.glob("*.tar.gz")) if bundles_dir.exists() else []
    if not tarballs:
        print("✖ 没有找到本地 bundle(remote/bundles/)。跳过上传,仍用 COS 直链。")
        return {}

    # 按 60 个一批拆成多个 release,规避单个 release 资产数量上限
    CHUNK = 60
    mapping = {}
    total = len(tarballs)
    for i in range(0, total, CHUNK):
        chunk = tarballs[i:i + CHUNK]
        tag = f"bundles-{i // CHUNK + 1:02d}-of-{(total + CHUNK - 1) // CHUNK:02d}"
        # 先删旧的同名 release,避免重复资产报错
        gh(["release", "delete", tag, "--repo", repo, "--yes", "--cleanup-tag"], err_ok=True)
        print(f"创建 release {tag} ({len(chunk)} 个资产)…")
        cmd = ["release", "create", tag, "--repo", repo,
               "--title", f"WorkBuddy 专家 Bundle 包 {i+1}-{i+len(chunk)}/{total}",
               "--notes", "WorkBuddy 领域专家/专家团 bundle(自托管镜像)。"]
        cmd += [str(p) for p in chunk]
        gh(cmd)
        for j, p in enumerate(chunk):
            plugin = p.name[:-7]  # 去 .tar.gz
            # 资产下载 URL 形如 https://github.com/o/r/releases/download/<tag>/<file>
            mapping[plugin] = f"https://github.com/{repo}/releases/download/{tag}/{p.name}"
    return mapping


def main():
    ap = argparse.ArgumentParser(description="发布 WorkBuddy 专家商城到云端(GitHub Pages + Release)")
    ap.add_argument("--repo", default=DEFAULT_REPO, help=f"目标仓库(默认 {DEFAULT_REPO})")
    ap.add_argument("--upload-bundles", action="store_true",
                    help="把 remote/bundles 的 bundle 上传为 GitHub Release 资产并重写数据(默认关闭,仍用 COS 直链)")
    ap.add_argument("--bundles-dir", type=Path, default=None, help="bundle 目录(默认 remote/bundles)")
    ap.add_argument("--keep-cos", action="store_true",
                    help="保留数据里的 COS bundleUrl(默认行为,与 --upload-bundles 同时给时以 --keep-cos 优先)")
    ap.add_argument("--inplace", action="store_true",
                    help="重写后的 expert-store.json 直接覆盖 storefront/data(否则只用于发布,本地不变)")
    ap.add_argument("--no-rebuild", action="store_true",
                    help="跳过重新生成 plugin-store.json / skill-registry.json(默认每次发布前重建,把本地插件/技能一起带上云端)")
    args = ap.parse_args()

    check_gh()

    # 发布前重建插件/技能数据 → 本地插件/技能随商城一起上云
    if not args.no_rebuild:
        print("\n=== 0/3 重建插件 + 技能数据(本地 → 云端) ===")
        scripts = ROOT / "scripts"
        for script in ("build-plugin-store.py", "build-skill-registry.py"):
            sp = scripts / script
            if sp.exists():
                sh(["python3", str(sp)])
            else:
                print(f"(跳过: 缺 {script})")

    # 读数据
    data = json.loads(DATA.read_text("utf-8"))

    # 可选:上传 bundles 并重写 URL
    rewritten = 0
    if args.upload_bundles and not args.keep_cos:
        bdir = args.bundles_dir or BUNDLES
        mapping = upload_bundles(args.repo, bdir)
        rewritten = rewrite_bundle_url(data, mapping)
        if rewritten:
            print(f"✔ 已重写 {rewritten} 个 bundleUrl → GitHub Release")
    else:
        print("\n(保留腾讯 COS 直链,数据不重写)")

    # 计算最终商城地址并更新 meta
    owner, name = args.repo.split("/")
    url = f"https://{owner}.github.io/{name}/"
    now = subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
                         capture_output=True, text=True).stdout.strip()
    data["meta"]["publishedAt"] = now
    data["meta"]["storeUrl"] = url
    data["meta"]["bundleHosting"] = "github-release" if rewritten else "tencent-cos"

    # 写发布用数据(临时目录里的一份)
    with tempfile.TemporaryDirectory(prefix="store-data-") as tmp:
        tmpd = Path(tmp)
        site_data = tmpd / "expert-store.json"
        site_data.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        if args.inplace and rewritten:
            DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
            print(f"✔ 已覆盖本地 {DATA}")
        # 发布时把这份数据放回 storefront/data(原 COS 版备份)
        orig = STOREFRONT / "data" / "expert-store.json"
        backup = STOREFRONT / "data" / "expert-store.cos.json"
        if not backup.exists():
            shutil.copy2(orig, backup)
        shutil.copy2(site_data, orig)

    publish_storefront(args.repo)

    print("\n=== 3/3 完成 ===")
    print(f"商城地址: {url}")
    print(f"数据专家数: {data['meta']['count']}(agent {data['meta']['agentCount']} / team {data['meta']['teamCount']})")
    if rewritten:
        print(f"bundleUrl 重写数: {rewritten}(全部自托管)")
    else:
        print("bundleUrl: 腾讯 COS 直链(未自托管)")
    print("\n用 --upload-bundles 可把 bundle 也自托管到 GitHub Release。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
