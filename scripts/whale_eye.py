"""鲸鱼之眼(whale_eye)视觉判读 — 给纯文本模型装的一只"眼睛"(CLI 包装)。

核心逻辑在 ``runtime/platform/plugins/bundled/whale_eye/service.py``
(同时注册为 ``whale_eye.read`` skill);本脚本只是把同一能力暴露成
CLI,方便在终端直接调用。

用法::

    # 直接读图
    .venv/bin/python scripts/whale_eye.py path/to/shot.png
    # 指定判读角度(默认是 UI 视觉回归 checklist)
    .venv/bin/python scripts/whale_eye.py shot.png --prompt "检查弹窗能否关闭"
    # 截图 + 判读一步到位(playwright 打开 URL 落盘后再判读)
    .venv/bin/python scripts/whale_eye.py --url http://localhost:3000

输出:判读文本打到 stdout,截图与报告落到 --output-dir(默认 .codex-logs/vision/)。
agns 配置复用 ``data/custom_models.json`` 的 agnes entry;可用
``AGNES_API_KEY`` / ``AGNES_BASE_URL`` / ``AGNES_VISION_MODEL`` 覆盖。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from runtime.platform.plugins.bundled.whale_eye.service import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PROMPT,
    VisionUnavailableError,
    judge,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("image", nargs="?", help="本地图片路径")
    src.add_argument("--url", help="URL:用 playwright 截图后再判读")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="判读提示词")
    parser.add_argument("--selector", default=None, help="只对页面上该元素截图")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    try:
        result = judge(
            image=args.image or "",
            url=args.url or "",
            prompt=args.prompt,
            selector=args.selector or "",
            output_dir=Path(args.output_dir),
            model=args.model,
        )
    except VisionUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if "error" in result:
        print(f"判读失败: {result['error']}", file=sys.stderr)
        return 1

    print(result["verdict"])
    print(
        f"\n[whale_eye] 判读已落盘: {result['report_path']} (截图: {result['image_path']})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

