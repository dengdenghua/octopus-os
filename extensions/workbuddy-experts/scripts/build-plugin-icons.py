#!/usr/bin/env python3
"""生成商城插件图标到 storefront/data/icons/ 并登记 icon 字段。

策略:
  - codex 插件:从 ~/.echo/plugins/codex/<id>/assets/ 抽主图标
    (优先级: icon.png > logo.png > composer-icon.png > app-icon.png > 任意 png/svg)
    拷为 icons/codex_<id>.<ext>
  - workbuddy connector:生成品牌色 SVG 占位图标(取名字首字符),icons/wb_<id>.svg
  - 重写 plugin-store.json:每个条目加 icon 相对路径

用法:
  python3 extensions/workbuddy-experts/scripts/build-plugin-icons.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CODEX_CACHE = Path.home() / ".echo" / "plugins" / "codex"
STORE_DATA = REPO / "extensions" / "workbuddy-experts" / "storefront" / "data"
ICONS_DIR = STORE_DATA / "icons"
STORE = STORE_DATA / "plugin-store.json"

# 连接器品牌色板(按 id hash 稳定取色)
_PALETTE = [
    "#2563EB",
    "#0EA5E9",
    "#10B981",
    "#F59E0B",
    "#EF4444",
    "#8B5CF6",
    "#EC4899",
    "#14B8A6",
    "#F97316",
    "#6366F1",
    "#84CC16",
    "#06B6D4",
]

_CODEX_ICON_PRIORITY = [
    "icon.png",
    "icon.svg",
    "logo.png",
    "logo.svg",
    "composer-icon.png",
    "composer-icon.svg",
    "app-icon.png",
    "app-icon.svg",
    "browser.png",
    "documents.png",
    "file-document.png",
]


def _pick_codex_icon(root: Path) -> Path | None:
    assets = root / "assets"
    if not assets.is_dir():
        return None
    for name in _CODEX_ICON_PRIORITY:
        p = assets / name
        if p.is_file():
            return p
    # 兜底:任意 png/svg(跳过多浏览器品牌图标如 brave/opera)
    for p in sorted(assets.iterdir()):
        if p.suffix.lower() in (".png", ".svg") and not any(
            b in p.name for b in ("brave", "opera", "vivaldi", "edge")
        ):
            return p
    return None


def _brand_color(plugin_id: str) -> str:
    h = int(hashlib.md5(plugin_id.encode()).hexdigest()[:8], 16)
    return _PALETTE[h % len(_PALETTE)]


def _initial(name_zh: str) -> str:
    if not name_zh:
        return "?"
    return name_zh.strip()[0].upper()


def _make_placeholder_svg(plugin_id: str, label: str, color: str, out: Path) -> None:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96">'
        f'<rect width="96" height="96" rx="22" fill="{color}"/>'
        f'<text x="48" y="60" font-size="38" font-weight="700" fill="#fff" '
        f'text-anchor="middle" font-family="-apple-system,Segoe UI,sans-serif">'
        f"{label}</text></svg>"
    )
    out.write_text(svg, "utf-8")


def attach_icons(store_path: Path | None = None) -> tuple[int, int]:
    """给 plugin-store.json 的每个条目附加 icon 字段,并生成图标文件。

    可被 build-plugin-store.py 复用(发布流程会重建 store,必须在这里再挂图标)。
    返回 (真实图标数, 占位生成数)。
    """
    store = store_path or STORE
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    data = json.loads(store.read_text("utf-8"))
    items = data["items"]

    made, copied = 0, 0
    for it in items:
        pid = it["id"]  # codex_<name> / wb_<id>
        icon_name = f"{pid}.svg"  # 默认生成 SVG(文件名)
        icon_rel = f"icons/{icon_name}"  # store 里的相对路径
        if it.get("source") == "codex":
            root = CODEX_CACHE / it["plugin"]
            src = _pick_codex_icon(root) if root.is_dir() else None
            if src:
                ext = src.suffix.lower()
                icon_name = f"{pid}{ext}"
                icon_rel = f"icons/{icon_name}"
                shutil.copy2(src, ICONS_DIR / icon_name)
                copied += 1
            else:
                # 无资产 → 品牌色占位
                _make_placeholder_svg(
                    pid,
                    _initial(it.get("name_zh") or pid),
                    _brand_color(pid),
                    ICONS_DIR / icon_name,
                )
                made += 1
        else:
            label = _initial(it.get("name_zh") or it.get("name") or pid)
            _make_placeholder_svg(pid, label, _brand_color(pid), ICONS_DIR / icon_name)
            made += 1
        it["icon"] = icon_rel

    data["meta"]["icons"] = f"{len(items)}(实图 {copied} / 生成 {made})"
    store.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    print(f"✔ {store} — 条目 {len(items)}: 真实图标 {copied},占位生成 {made}")
    return copied, made


if __name__ == "__main__":
    attach_icons()
