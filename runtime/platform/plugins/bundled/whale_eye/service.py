"""鲸鱼之眼(whale_eye)视觉判读核心服务 — 给纯文本模型装的一只"眼睛"。

把一张图(本地文件 / URL)交给 agnes-2.5-flash(``supports_vision: true``，
OpenAI 兼容端点)做视觉判读，输出中文文本。这样跑在纯文本模型上的 agent
也能"看见"截图，用于视觉回归辅助；``describe_image`` 是视觉守卫
(vision_guard)的转述入口，给纯文本模型喂图前把图变成一段文字描述。

配置复用 ``data/custom_models.json`` 的 agnes entry；可用
``AGNES_API_KEY`` / ``AGNES_BASE_URL`` / ``AGNES_VISION_MODEL`` 环境变量覆盖。
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

from runtime.safety.auth.url_guard import safe_httpx_request

_logger = logging.getLogger(__name__)

DEFAULT_PROMPT = (
    "你是资深前端工程师做视觉回归。请仔细查看这张界面截图，逐项检查并输出中文结论：\n"
    "1. 布局：有无元素溢出、重叠、错位、被裁切；\n"
    "2. 文本：有无文案截断(…)、被遮挡、字号层级混乱；\n"
    "3. 样式：有无明显颜色/对比度问题、组件样式不统一；\n"
    "4. 状态：有无空白区、加载态未结束、渲染错误。\n"
    "先给总体结论(正常/有问题)，再按严重程度列出具体问题及大致位置，最后给修复建议。"
)

DEFAULT_OUTPUT_DIR = Path(".codex-logs/vision")

# 视觉守卫转述提示词:把图变成一段让纯文本模型能行动的说明,而非 UI 回归 checklist。
DESCRIBE_PROMPT = (
    "用简洁中文描述这张图片的可见内容:主体、文字、布局、界面元素等。"
    "目的是让一个纯文本模型仅凭这段描述就能理解这张图。"
)

# agnes-2.5-flash 是 reasoning 模型,复杂截图推理会占掉大量预算;
# 预留给判读 content 的空间,否则偶发空 content(usage.reasoning_tokens 打满)。
_MAX_TOKENS = 8192

_AGNES_BASE_URL_DEFAULT = "https://apihub.agnes-ai.com/v1"
_AGNES_VISION_MODEL_DEFAULT = "agnes-2.5-flash"


class VisionUnavailableError(RuntimeError):
    """agnes 视觉能力不可用(未配置 api_key 等)。"""


def _load_agnes_entry() -> dict[str, Any]:
    """从 custom_models.json 读 agnes vision entry(与运行时同一份配置)。"""
    candidates: list[dict[str, Any]] = []
    for path in (
        Path("data/custom_models.json"),
        Path.home() / ".echo" / "custom_models.json",
    ):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                candidates.append(data)
    for data in candidates:
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            models = entry.get("models") or []
            if isinstance(models, list) and any(str(m).startswith("agnes-") for m in models):
                return entry
            if str(entry.get("base_url") or "").find("agnes-ai.com") >= 0:
                return entry
    return {}


def _pick_vision_model(entry: dict[str, Any]) -> str | None:
    models = entry.get("models") or []
    if isinstance(models, list):
        for m in models:
            name = str(m or "")
            if name and name != "agnes-image-2.1-flash" and not name.endswith("-image"):
                return name
    return None


def resolve_config(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, str]:
    """解析 agnes 调用配置;缺 api_key 时抛 VisionUnavailableError。"""
    entry = _load_agnes_entry()
    resolved_base = (
        (base_url or "").strip()
        or os.environ.get("AGNES_BASE_URL", "").strip()
        or str(entry.get("base_url") or "").strip()
        or _AGNES_BASE_URL_DEFAULT
    ).rstrip("/")
    resolved_model = (
        (model or "").strip()
        or os.environ.get("AGNES_VISION_MODEL", "").strip()
        or _pick_vision_model(entry)
        or _AGNES_VISION_MODEL_DEFAULT
    )
    resolved_key = (
        (api_key or "").strip()
        or os.environ.get("AGNES_API_KEY", "").strip()
        or str(entry.get("api_key") or "").strip()
    )
    if not resolved_key:
        raise VisionUnavailableError(
            "未找到 agnes api_key:请设置 AGNES_API_KEY,或确认 data/custom_models.json "
            "含 agnes entry。"
        )
    return {"base_url": resolved_base, "model": resolved_model, "api_key": resolved_key}


def image_to_data_url(image_path: Path) -> str:
    """本地图片 → data URL,喂给 OpenAI 兼容视觉端点。"""
    mime, _ = mimetypes.guess_type(str(image_path))
    mime = mime or "image/png"
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _chat(
    cfg: dict[str, str],
    image_url: str,
    prompt: str,
    *,
    max_tokens: int = _MAX_TOKENS,
    timeout: int = 180,
) -> str:
    """调用 agnes 视觉模型;偶发空 content 时自动重试一次。

    ``timeout`` 默认 180s(视觉回归可等);视觉守卫走短超时,避免
    阻塞主回合。
    """
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": max_tokens,
    }
    endpoint = f"{cfg['base_url']}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['api_key']}",
    }
    for attempt in range(2):
        # The endpoint is operator-owned model configuration and may point at
        # a loopback inference server. Redirects stay disabled so its bearer
        # credential can never be forwarded to another origin.
        response = safe_httpx_request(
            "POST",
            endpoint,
            json=payload,
            headers=headers,
            timeout=timeout,
            allow_private=True,
            follow_redirects=False,
            read_cap_bytes=8 * 1024 * 1024,
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"] or ""
        if content.strip() or attempt == 1:
            return content
    return ""


def describe_image(
    image_b64: str = "",
    *,
    image_url: str = "",
    prompt: str = DESCRIBE_PROMPT,
    max_tokens: int = 1024,
    timeout: int = 30,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> str | None:
    """把一张图转成文本描述,供纯文本模型"看"图(视觉守卫转述入口)。

    ``image_b64`` 是裸 base64(``ModelRequest.images_b64`` 通道);
    ``image_url`` 是 data: URL 或 https URL(内联 image_url 块通道),
    两者至少给一个。

    Returns ``None`` 当 agnes 未配置、调用失败或返回空——调用方此时
    应直接丢弃图片并加注记,而不是让回合崩溃。

    转述是一次尽力而为的远程调用,实测偶发超时/网络抖动会返回空;
    ``_chat`` 失败或返回空时自动重试一次(第二次几乎总是成功),
    仍失败才返回 None。
    """
    try:
        cfg = resolve_config(api_key=api_key, base_url=base_url, model=model)
    except VisionUnavailableError:
        return None
    if image_b64 and not image_url:
        image_url = f"data:image/png;base64,{image_b64}"
    if not image_url:
        return None
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            verdict = _chat(cfg, image_url, prompt, max_tokens=max_tokens, timeout=timeout)
            if verdict and verdict.strip():
                return verdict.strip()
            last_error = None
        except Exception as exc:  # noqa: BLE001 — 转述尽力而为,任何失败都不该毁掉主回合
            last_error = exc
    if last_error is not None:
        _logger.debug("describe_image failed after retry: %s", last_error)
    return None


def _shot(url: str, output_dir: Path, selector: str | None) -> Path:
    """playwright 打开 URL 截图;实时应用常驻 websocket/SSE,networkidle 永不触发。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - tooling gate
        raise VisionUnavailableError(
            "截图需要 playwright:.venv/bin/pip install playwright"
        ) from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    shot = output_dir / "shot.png"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(url, wait_until="load", timeout=60_000)
        page.wait_for_timeout(1500)
        if selector:
            page.locator(selector).first.screenshot(path=str(shot))
        else:
            page.screenshot(path=str(shot))
        browser.close()
    return shot


def judge(
    image: str = "",
    *,
    url: str = "",
    prompt: str = DEFAULT_PROMPT,
    selector: str = "",
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """判读一张图(本地文件或 URL),返回结构化结果。

    参数:
      image: 本地图片路径(与 url 二选一)。
      url: 用 playwright 截图后再判读。
      prompt: 判读提示词,默认 UI 视觉回归 checklist。
      selector: 只对页面上该元素截图。
      output_dir: 截图/报告落盘目录。
      model / api_key / base_url: 覆盖 agnes 配置。

    返回 dict(供 skill handler 直接转成 agent 可见结果):
      error: 判读失败时的错误信息(其余字段缺省)。
      verdict / image_path / report_path / model: 成功时。
    """
    # 先准备图片,再解析 agnes 配置——路径错误/截图失败是最常见的输入问题,
    # 不应被"缺 api_key"这类外部配置错误掩盖。cfg 在真正调用 agnes 前才解析。
    if not image and not url:
        return {"error": "需要 image(本地图片路径)或 url(截图来源),两者至少给一个"}

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if not url:
        image_path = Path(image).expanduser().resolve()
        if not image_path.exists():
            return {"error": f"图片不存在: {image_path}"}
        if out != image_path.parent:
            dest = out / image_path.name
            dest.write_bytes(image_path.read_bytes())
            image_path = dest
    else:
        try:
            image_path = _shot(url, out, selector or None)
        except VisionUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 — 截图失败转成 skill 可见错误
            return {"error": f"截图失败: {exc}"}

    cfg = resolve_config(api_key=api_key, base_url=base_url, model=model)
    resolved_model = cfg["model"]
    image_url = image_to_data_url(image_path)
    verdict = _chat(cfg, image_url, prompt)
    if not verdict.strip():
        return {"error": "agnes 返回空判读(重试后仍为空)"}

    report = out / "verdict.md"
    report.write_text(
        f"# 视觉判读\n\n- 图片: {image_path}\n- 模型: {resolved_model}\n- 提示: {prompt}\n\n{verdict}\n",
        encoding="utf-8",
    )
    return {
        "verdict": verdict,
        "image_path": str(image_path),
        "report_path": str(report),
        "model": resolved_model,
    }


__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PROMPT",
    "DESCRIBE_PROMPT",
    "VisionUnavailableError",
    "describe_image",
    "image_to_data_url",
    "judge",
    "resolve_config",
]
