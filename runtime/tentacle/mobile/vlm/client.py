"""VlmClient —— 裸 HTTP 调 OpenAI 兼容 VLM（视觉语言模型）API.

与 LightweightLlmClient 风格完全对称：

- **零外部依赖**：用标准库 ``urllib.request``，不引入 ``requests`` / ``httpx``
- **OpenAI 兼容**：GPT-4o / Qwen-VL / GLM-4V / DeepSeek-VL 全部可对接
- **同步阻塞**：适合母体 Worker 直接调用
- **可注入 transport**：测试时换成 ``FakeVlmTransport`` 不发真请求

核心能力：
- 分析截图 → 返回屏幕描述 + 建议操作
- 结合无障碍树 → 更精准的操作建议
- 操作前后截图对比 → 验证操作结果
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ── Transport 协议（可注入，便于测试） ──────────────────────────


class VlmTransport(Protocol):
    """VLM HTTP transport 协议 —— 任何"接受 url+headers+body，返回 dict"的对象都行."""

    def post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]: ...


class UrllibVlmTransport:
    """默认 transport：标准库 ``urllib.request``."""

    def post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310 — audited HTTP VLM endpoint
            payload = resp.read().decode("utf-8")
        return json.loads(payload)


# ── 配置 ──────────────────────────────────────────────────────


@dataclass(slots=True)
class VlmConfig:
    """VLM 配置 —— 一次构造，多处复用.

    预设工厂方法覆盖主流 OpenAI 兼容视觉模型供应商：

    - :meth:`deepseek_vl`  DeepSeek-VL
    - :meth:`qwen_vl`      阿里 Qwen-VL
    - :meth:`glm_vl`       智谱 GLM-4V
    - :meth:`openai_vl`    OpenAI GPT-4o
    """

    base_url: str
    api_key: str
    model: str
    max_tokens: int = 1024
    temperature: float = 0.1
    timeout_s: float = 120.0
    max_retries: int = 2
    retry_delay_s: float = 1.0

    # ── 预设工厂 ────────────────────────────────────────────

    @classmethod
    def deepseek_vl(cls, api_key: str | None = None, model: str = "deepseek-vl") -> VlmConfig:
        """DeepSeek-VL 配置."""
        return cls(
            base_url="https://api.deepseek.com/v1",
            api_key=api_key or os.environ.get("DEEPSEEK_API_KEY", ""),
            model=model,
        )

    @classmethod
    def qwen_vl(cls, api_key: str | None = None, model: str = "qwen-vl-max") -> VlmConfig:
        """阿里 Qwen-VL 配置."""
        return cls(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=api_key or os.environ.get("QWEN_API_KEY", ""),
            model=model,
        )

    @classmethod
    def glm_vl(cls, api_key: str | None = None, model: str = "glm-4v") -> VlmConfig:
        """智谱 GLM-4V 配置."""
        return cls(
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key=api_key or os.environ.get("GLM_API_KEY", ""),
            model=model,
        )

    @classmethod
    def openai_vl(cls, api_key: str | None = None, model: str = "gpt-4o") -> VlmConfig:
        """OpenAI GPT-4o 配置."""
        return cls(
            base_url="https://api.openai.com/v1",
            api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
            model=model,
        )

    def require_key(self) -> None:
        """校验 API Key 是否已设置."""
        if not self.api_key or not self.api_key.strip():
            raise ValueError(
                f"VLM api_key missing for model={self.model!r}. Set env var or pass explicitly."
            )


# ── 数据类型 ──────────────────────────────────────────────────


@dataclass(slots=True)
class SuggestedAction:
    """VLM 建议的操作."""

    action: str  # 如 "tap", "swipe", "type", "scroll"
    target: str  # 目标描述（如 "登录按钮"）
    coordinates: tuple[int, int] | None = None  # 建议坐标 (x, y)
    text: str | None = None  # 输入文本（type 时）
    confidence: float = 0.0  # 置信度 0-1

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "action": self.action,
            "target": self.target,
            "confidence": self.confidence,
        }
        if self.coordinates is not None:
            d["coordinates"] = list(self.coordinates)
        if self.text is not None:
            d["text"] = self.text
        return d


@dataclass(slots=True)
class ScreenAnalysis:
    """VLM 屏幕分析结果."""

    description: str  # 屏幕内容描述
    suggested_actions: list[SuggestedAction]  # 建议的操作列表
    current_app: str | None = None  # 识别出的当前 App
    screen_state: str = ""  # 屏幕状态摘要

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "suggested_actions": [a.to_dict() for a in self.suggested_actions],
            "current_app": self.current_app,
            "screen_state": self.screen_state,
        }


@dataclass(slots=True)
class VlmUsage:
    """VLM token 使用量统计."""

    total_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_latency_ms: int = 0

    def record(self, prompt_tokens: int, completion_tokens: int, latency_ms: int) -> None:
        """记录一次调用的 token 使用量."""
        self.total_calls += 1
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.total_tokens += prompt_tokens + completion_tokens
        self.total_latency_ms += latency_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
        }


# ── 客户端 ────────────────────────────────────────────────────


class VlmClient:
    """VLM 视觉理解客户端.

    支持所有 OpenAI 兼容的视觉模型（GPT-4o, Qwen-VL, GLM-4V, DeepSeek-VL 等）。
    零外部依赖，裸 urllib 发送请求。

    用法::

        client = VlmClient(VlmConfig.qwen_vl("sk-xxx"))
        result = client.analyze_screenshot(
            screenshot_base64="...",
            task="找到登录按钮并点击",
            screen_info={...},  # 可选，无障碍树信息
        )
    """

    def __init__(
        self,
        config: VlmConfig,
        transport: VlmTransport | None = None,
    ) -> None:
        self.config = config
        self.transport: VlmTransport = transport or UrllibVlmTransport()
        config.require_key()
        self.usage = VlmUsage()

    # ── 主入口 ──────────────────────────────────────────────

    def analyze_screenshot(
        self,
        screenshot_base64: str,
        task: str,
        screen_info: dict[str, Any] | None = None,
    ) -> ScreenAnalysis:
        """分析截图，返回建议操作.

        Args:
            screenshot_base64: 截图的 base64 编码字符串
            task: 当前任务描述（如 "找到登录按钮并点击"）
            screen_info: 可选，无障碍树信息（get_screen_info 返回值）

        Returns:
            :class:`ScreenAnalysis`，含屏幕描述 + 建议操作列表
        """
        # 构造用户消息：图片 + 任务描述
        user_content: list[dict[str, Any]] = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{screenshot_base64}",
                },
            },
        ]

        # 拼接任务描述 + 可选的无障碍树信息
        text_parts = [f"当前任务：{task}"]
        if screen_info:
            tree_text = json.dumps(screen_info, ensure_ascii=False, indent=2)
            # 截断过长的无障碍树，避免爆 token
            if len(tree_text) > 3000:
                tree_text = tree_text[:3000] + "\n... [截断]"
            text_parts.append(f"无障碍树信息：\n{tree_text}")
        text_parts.append(
            "请分析当前屏幕内容，描述你看到的界面，并建议下一步操作。"
            "\n请以 JSON 格式返回，包含以下字段："
            "\n- description: 屏幕内容描述"
            "\n- suggested_actions: 建议操作列表，每项包含 action（tap/swipe/type/scroll）、"
            "target（目标描述）、coordinates（[x,y]坐标，可选）、text（输入文本，可选）、"
            "confidence（置信度0-1）"
            "\n- current_app: 当前应用名称（如能识别）"
            "\n- screen_state: 屏幕状态摘要（如'登录页'、'首页'、'弹窗遮挡'等）"
        )

        user_content.append(
            {
                "type": "text",
                "text": "\n".join(text_parts),
            }
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个移动设备屏幕分析助手。你需要根据截图分析当前屏幕内容，"
                    "并为用户的任务建议下一步操作。请始终以合法的 JSON 格式返回结果。"
                ),
            },
            {
                "role": "user",
                "content": user_content,
            },
        ]

        return self._call_vlm(messages)

    def analyze_with_tree(
        self,
        screenshot_base64: str,
        tree_data: dict[str, Any],
        task: str,
    ) -> ScreenAnalysis:
        """结合无障碍树分析截图.

        与 analyze_screenshot 类似，但强制使用无障碍树数据，
        并在 prompt 中强调结合树信息进行更精准的分析。

        Args:
            screenshot_base64: 截图的 base64 编码字符串
            tree_data: 无障碍树数据（get_screen_info 返回值）
            task: 当前任务描述

        Returns:
            :class:`ScreenAnalysis`
        """
        tree_text = json.dumps(tree_data, ensure_ascii=False, indent=2)
        if len(tree_text) > 5000:
            tree_text = tree_text[:5000] + "\n... [截断]"

        user_content: list[dict[str, Any]] = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{screenshot_base64}",
                },
            },
            {
                "type": "text",
                "text": (
                    f"当前任务：{task}\n\n"
                    f"无障碍树信息：\n{tree_text}\n\n"
                    "请结合截图和无障碍树信息，精准分析当前屏幕内容，并建议下一步操作。"
                    "\n优先使用无障碍树中的坐标和文本信息来定位元素。"
                    "\n请以 JSON 格式返回，包含以下字段："
                    "\n- description: 屏幕内容描述"
                    "\n- suggested_actions: 建议操作列表，每项包含 action（tap/swipe/type/scroll）、"
                    "target（目标描述）、coordinates（[x,y]坐标，可选）、text（输入文本，可选）、"
                    "confidence（置信度0-1）"
                    "\n- current_app: 当前应用名称"
                    "\n- screen_state: 屏幕状态摘要"
                ),
            },
        ]

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个移动设备屏幕分析助手，擅长结合无障碍树和截图进行精准分析。"
                    "请始终以合法的 JSON 格式返回结果。"
                    "优先使用无障碍树中的坐标和文本信息来定位元素。"
                ),
            },
            {
                "role": "user",
                "content": user_content,
            },
        ]

        return self._call_vlm(messages)

    def verify_action(
        self,
        screenshot_before_base64: str,
        screenshot_after_base64: str,
        expected_result: str,
    ) -> bool:
        """验证操作结果（操作前后截图对比）.

        Args:
            screenshot_before_base64: 操作前截图的 base64 编码
            screenshot_after_base64: 操作后截图的 base64 编码
            expected_result: 期望的操作结果描述

        Returns:
            True 表示操作结果符合预期，False 表示不符合
        """
        user_content: list[dict[str, Any]] = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{screenshot_before_base64}",
                },
            },
            {
                "type": "text",
                "text": "这是操作前的截图。",
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{screenshot_after_base64}",
                },
            },
            {
                "type": "text",
                "text": (
                    f"这是操作后的截图。\n\n"
                    f"期望的操作结果：{expected_result}\n\n"
                    "请判断操作后的截图是否符合期望结果。"
                    '\n请以 JSON 格式返回：{"verified": true/false, "reason": "判断理由"}'
                ),
            },
        ]

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个操作验证助手。你需要对比操作前后的截图，"
                    "判断操作是否达到了期望的结果。请始终以合法的 JSON 格式返回结果。"
                ),
            },
            {
                "role": "user",
                "content": user_content,
            },
        ]

        try:
            raw_text = self._call_vlm_raw(messages)
            result = self._parse_json_response(raw_text)
            return bool(result.get("verified", False))
        except Exception as e:
            logger.warning("VLM verify_action 解析失败: %s", e)
            return False

    # ── 内部方法 ────────────────────────────────────────────

    def _call_vlm(self, messages: list[dict[str, Any]]) -> ScreenAnalysis:
        """调用 VLM API 并解析为 ScreenAnalysis."""
        raw_text = self._call_vlm_raw(messages)
        return self._parse_screen_analysis(raw_text)

    def _call_vlm_raw(self, messages: list[dict[str, Any]]) -> str:
        """调用 VLM API 并返回原始文本响应（带重试）."""
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.config.api_key}"}

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            start = time.time()
            try:
                payload = self.transport.post(url, headers, body)
                latency_ms = int((time.time() - start) * 1000)

                # 解析响应
                try:
                    choice = payload["choices"][0]
                except (KeyError, IndexError):
                    logger.error("VLM 响应缺少 choices: %s", payload)
                    raise ValueError("VLM 响应格式异常：缺少 choices") from None

                content = choice.get("message", {}).get("content", "")

                # 记录 token 使用量
                usage_data = payload.get("usage", {}) or {}
                self.usage.record(
                    prompt_tokens=int(usage_data.get("prompt_tokens", 0)),
                    completion_tokens=int(usage_data.get("completion_tokens", 0)),
                    latency_ms=latency_ms,
                )

                logger.debug(
                    "VLM 调用成功 model=%s latency=%dms tokens=%d",
                    self.config.model,
                    latency_ms,
                    usage_data.get("total_tokens", 0),
                )
                return content

            except urllib.error.HTTPError as e:
                latency_ms = int((time.time() - start) * 1000)
                # 尝试解析错误体
                try:
                    err = json.loads(e.read().decode("utf-8"))
                except Exception:  # noqa: BLE001 — best-effort; fail-open
                    err = {"error": {"message": str(e)}}
                logger.error(
                    "VLM HTTP %s (attempt %d/%d): %s",
                    e.code,
                    attempt + 1,
                    self.config.max_retries + 1,
                    err,
                )
                last_error = e
                # 429 / 5xx 可重试
                if e.code not in (429, 500, 502, 503, 504):
                    raise
            except urllib.error.URLError as e:
                latency_ms = int((time.time() - start) * 1000)
                logger.error(
                    "VLM URL error (attempt %d/%d): %s",
                    attempt + 1,
                    self.config.max_retries + 1,
                    e,
                )
                last_error = e

            # 重试等待
            if attempt < self.config.max_retries:
                wait = self.config.retry_delay_s * (attempt + 1)
                logger.info("VLM 重试等待 %.1fs...", wait)
                time.sleep(wait)

        # 所有重试失败
        raise RuntimeError(f"VLM 调用失败（已重试 {self.config.max_retries} 次）: {last_error}")

    def _parse_screen_analysis(self, raw_text: str) -> ScreenAnalysis:
        """将 VLM 返回的原始文本解析为 ScreenAnalysis."""
        data = self._parse_json_response(raw_text)

        # 解析 suggested_actions
        actions: list[SuggestedAction] = []
        for act_data in data.get("suggested_actions", []):
            if not isinstance(act_data, dict):
                continue
            coords = act_data.get("coordinates")
            if coords and isinstance(coords, (list, tuple)) and len(coords) >= 2:
                coords = (int(coords[0]), int(coords[1]))
            else:
                coords = None
            actions.append(
                SuggestedAction(
                    action=str(act_data.get("action", "")),
                    target=str(act_data.get("target", "")),
                    coordinates=coords,
                    text=act_data.get("text"),
                    confidence=float(act_data.get("confidence", 0.0)),
                )
            )

        return ScreenAnalysis(
            description=str(data.get("description", "")),
            suggested_actions=actions,
            current_app=data.get("current_app"),
            screen_state=str(data.get("screen_state", "")),
        )

    @staticmethod
    def _parse_json_response(raw_text: str) -> dict[str, Any]:
        """从 VLM 原始文本中提取 JSON 对象.

        VLM 可能返回 markdown 代码块包裹的 JSON，需要兼容处理。
        """
        text = raw_text.strip()

        # 尝试直接解析
        try:
            return json.loads(text)
        except (
            json.JSONDecodeError
        ):  # expected · falls through to the code-block/brace extraction below
            pass

        # 尝试提取 ```json ... ``` 代码块
        import re

        json_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_block:
            try:
                return json.loads(json_block.group(1).strip())
            except json.JSONDecodeError:  # expected · falls through to the brace extraction below
                pass

        # 尝试提取第一个 { ... } 块
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start : brace_end + 1])
            except (
                json.JSONDecodeError
            ):  # expected · falls through to the logged safe-default below
                pass

        logger.warning("VLM 返回内容无法解析为 JSON: %s", text[:200])
        return {
            "description": text,
            "suggested_actions": [],
            "current_app": None,
            "screen_state": "unknown",
        }


# ── Fake Transport（测试用） ──────────────────────────────────


class FakeVlmTransport:
    """测试用 transport：预设响应序列，按调用顺序返回."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"url": url, "headers": headers, "body": body})
        if not self._responses:
            # 返回默认空响应
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "description": "测试屏幕",
                                    "suggested_actions": [],
                                    "current_app": None,
                                    "screen_state": "test",
                                }
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                "model": "test-vlm",
            }
        return self._responses.pop(0)
