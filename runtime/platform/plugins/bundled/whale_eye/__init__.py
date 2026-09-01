"""鲸鱼之眼(whale_eye)视觉插件 — 给纯文本模型装的一只"眼睛"。

注册 ``whale_eye.read`` skill:把一张图(本地文件 / URL)交给
agnes-2.5-flash(``supports_vision: true``)做视觉判读,返回中文文本。
跑在纯文本模型上的 agent 也能"看见"截图,用于视觉回归辅助;
视觉守卫(vision_guard)转述图片走同一插件的 ``describe_image``。
"""

from __future__ import annotations

import contextlib
from typing import Any

from runtime.execution.suckers.registry import Skill
from runtime.platform.plugins.plugin_base import ModulePlugin

from .service import DEFAULT_PROMPT, judge

_HANDLER_DESCRIPTION = (
    "读图并输出中文判读。把一张截图(本地图片路径 image,或 url 由本工具截图)"
    "交给 agnes 视觉模型做 UI 视觉回归:检查布局溢出/重叠/错位、文案截断、"
    "对比度/样式问题、加载态等。返回中文判读文本,agent 据此判断页面渲染是否正常。"
    "纯文本模型用它'看'图。"
)


def _read_vision(
    image: str = "",
    *,
    url: str = "",
    prompt: str = DEFAULT_PROMPT,
    selector: str = "",
    output_dir: str = "",
    model: str = "",
    **_: Any,
) -> dict[str, Any]:
    """鲸鱼之眼视觉判读 skill handler(image 与 url 二选一)。"""
    try:
        return judge(
            image=image,
            url=url,
            prompt=prompt,
            selector=selector,
            output_dir=output_dir or ".codex-logs/vision",
            model=model or None,
        )
    except Exception as exc:  # noqa: BLE001 — 任何失败都转成 skill 可见错误
        return {"error": str(exc)}


class WhaleEyePlugin(ModulePlugin):
    name = "whale_eye"
    display_name = "鲸鱼之眼"
    version = "1.0.0"
    description = "鲸鱼之眼 — 视觉判读与转述(截图 → 中文判读/描述,给纯文本模型装的眼睛)"
    author = "Echo"

    def register_skills(self) -> None:
        if self.ctx is None:
            return
        with contextlib.suppress(Exception):
            self.ctx.register_skill(
                Skill(
                    name="whale_eye.read",
                    description=_HANDLER_DESCRIPTION,
                    summary="读图判读:把截图交给 agnes 视觉模型,返回中文 UI 视觉回归结论",
                    affinity=["vision", "ui", "qa", "screenshot"],
                    cost_profile="high",
                    trusted_source="plugin://whale_eye",
                    handler=_read_vision,
                )
            )


__all__ = ["WhaleEyePlugin"]
