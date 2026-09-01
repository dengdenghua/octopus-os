"""SkillManifestLoader —— SKILL.md frontmatter 解析器.

与 Echo Mobile 端 ``SkillManifest.kt`` 对称：

- **零外部依赖**：不引入 ``pyyaml`` / ``python-frontmatter``
- **手写 YAML 子集解析**：只支持 ``key: value`` 和嵌套字典
- **自动构造 OpenAI tools 格式**：解析结果能直接喂给 :class:`LightweightLlmClient`
- **支持目录扫描**：从 ``runtime/tentacle/mobile/skills/`` 批量加载

SKILL.md 单源原则（见 :ref:`ADR-008 <adr-008>`）：

- 母体（Python 端）扫描目录 → 构造 SkillSpec 列表 → 喂给 LLM
- 手机端（Kotlin 端）扫描 assets/ → 构造等价 SkillSpec → 也喂给 LLM
- 两边解析器独立实现，但**输出一致**（同 JSON Schema）

YAML 最小子集：

.. code-block:: yaml

    name: android.tap
    description: 点击屏幕指定坐标
    timeout_ms: 15000
    risk: low
    requires_screen: true
    parameters:
      type: object
      properties:
        x:
          type: integer
          description: 屏幕 X 坐标
        y:
          type: integer
          description: 屏幕 Y 坐标
      required: [x, y]
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .chat_types import SkillSpec

logger = logging.getLogger(__name__)


# YAML frontmatter 分隔符
_FRONTMATTER_RE = re.compile(
    r"\A\s*---\s*\n(.*?)\n---\s*(?:\n(.*))?\Z",
    re.DOTALL,
)


@dataclass(slots=True)
class _ParsedDoc:
    meta: dict[str, Any]
    body: str


# ── 公开 API ──────────────────────────────────────────────────


class SkillManifestLoader:
    """SKILL.md 加载器.

    用法::

        loader = SkillManifestLoader()
        skills = loader.load_directory(Path("runtime/tentacle/mobile/skills"))
        # 把 skills 喂给 LightweightLlmClient.chat(messages, skills=skills)

    也可加载单文件::

        spec = loader.load_file(Path(".../tap.md"))
    """

    def load_file(self, path: str | os.PathLike[str]) -> SkillSpec:
        """加载单个 SKILL.md 文件."""
        text = Path(path).read_text(encoding="utf-8")
        return self.parse(text, source=str(path))

    def load_directory(
        self,
        dir_path: str | os.PathLike[str],
        *,
        pattern: str = "*.md",
        recursive: bool = True,
    ) -> list[SkillSpec]:
        """批量加载目录下所有 SKILL.md.

        Args:
            dir_path: 目录路径
            pattern: glob 模式，默认 ``*.md``
            recursive: 是否递归子目录（默认 True）

        Returns:
            解析成功的 :class:`SkillSpec` 列表（失败的记 warning 跳过）
        """
        root = Path(dir_path)
        if not root.exists():
            logger.warning("Skill dir not found: %s", root)
            return []
        if not root.is_dir():
            logger.warning("Not a directory: %s", root)
            return []

        paths: Iterable[Path]
        paths = sorted(root.rglob(pattern)) if recursive else sorted(root.glob(pattern))

        specs: list[SkillSpec] = []
        for p in paths:
            # 跳过 README / 顶层 SKILL.md / _meta.json 这类元文件
            # 注意：只跳过目录根下的 SKILL.md（索引文件），不跳过子目录中的 */SKILL.md（技能文件）
            if p.name.lower() == "readme.md":
                continue
            if p.name.lower() == "_meta.json":
                continue
            if p.name.lower() == "skill.md" and p.parent == root:
                continue
            try:
                spec = self.load_file(p)
                specs.append(spec)
            except Exception as e:
                logger.warning("Failed to parse SKILL.md %s: %s", p, e)
        logger.info("Loaded %d skills from %s", len(specs), root)
        return specs

    def parse(self, text: str, *, source: str = "<inline>") -> SkillSpec:
        """从字符串解析 SKILL.md.

        Args:
            text: 文件全文
            source: 源标识（用于错误信息）

        Raises:
            ValueError: frontmatter 缺失或缺 name
        """
        doc = self._split_frontmatter(text)
        meta = doc.meta

        if "name" not in meta:
            raise ValueError(f"SKILL.md {source!r} missing required field 'name'")
        if "description" not in meta:
            raise ValueError(f"SKILL.md {source!r} missing required field 'description'")

        return SkillSpec(
            name=str(meta["name"]),
            description=str(meta["description"]),
            parameters=meta.get("parameters", {}) or {},
            timeout_ms=int(meta.get("timeout_ms", 15_000)),
            risk=str(meta.get("risk", "low")),
            requires_screen=bool(meta.get("requires_screen", True)),
            body=doc.body,
        )

    def to_openai_tools(self, specs: list[SkillSpec]) -> list[dict[str, Any]]:
        """把 :class:`SkillSpec` 列表转成 OpenAI ``tools`` 数组."""
        return [s.to_openai_tool() for s in specs]

    # ── 内部：YAML 子集解析 ─────────────────────────────────

    def _split_frontmatter(self, text: str) -> _ParsedDoc:
        """拆分 frontmatter 与正文."""
        m = _FRONTMATTER_RE.match(text)
        if not m:
            raise ValueError(
                "SKILL.md must start with '---' frontmatter block. "
                f"Got first 80 chars: {text[:80]!r}"
            )
        meta_raw, body = m.group(1), (m.group(2) or "")
        meta = _parse_yaml_subset(meta_raw)
        return _ParsedDoc(meta=meta, body=body)


# ── YAML 最小子集解析器 ────────────────────────────────────────


def _parse_yaml_subset(text: str) -> dict[str, Any]:
    """手写 YAML 解析（只覆盖 SKILL.md 实际需要的子集）.

    支持：

    - ``key: value`` （value 是字符串 / 数字 / bool / null）
    - 嵌套字典（缩进）
    - 单行 list：``required: [x, y]`` 或 ``required: ["a", "b"]``
    - JSON 字面量：``parameters: {...}``
    - description 续行（YAML plain scalar continuation 和 ``|`` 块）
    - list-of-objects ``parameters: - name: x`` → 最佳努力的 JSON Schema

    不支持：anchor、tag、引用 —— SKILL.md 不需要。
    """
    # 预处理：把 description 续行（YAML plain scalar）和 | 块合并成单字符串
    text = _preprocess_yaml(text)
    root: dict[str, Any] = {}
    # 用一个栈管理"当前父级 + 缩进"
    # 元素: (indent, container_dict)
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for line_no, raw_line in enumerate(text.splitlines(), 1):
        # 跳过空行 / 纯注释
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        # 弹栈：找到 indent <= 当前缩进的父级
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]

        # 解析 "key: value"
        if ":" not in stripped:
            logger.warning("YAML line %d ignored (no colon): %r", line_no, raw_line)
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        if not value:
            # 嵌套字典的开始，下一行会填充
            new_dict: dict[str, Any] = {}
            parent[key] = new_dict
            stack.append((indent, new_dict))
        else:
            parent[key] = _parse_scalar(value)

    return root


def _preprocess_yaml(text: str) -> str:
    """预处理：合并 description 续行 + 把 list-of-objects 转成单行.

    解决两种 SKILL.md 实际写法：

    1. YAML plain scalar continuation（description 后无 | 标记，缩进续行）::

        description: 屏幕点击工具
          接受 x/y 坐标
          立即返回

    2. YAML literal block scalar（``description: |``）::

        description: |
          屏幕点击工具
          接受 x/y 坐标

    两种都合并为单行空格分隔。
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 模式 1 & 2: description 后跟多行内容
        # 检测 "description: |" 或 "description: ..."（后者如果下一行有缩进就续行）
        if stripped.startswith("description:") or stripped.startswith("summary:"):
            key_part, _, value_part = stripped.partition(":")
            value_part = value_part.strip()
            indent_base = len(line) - len(line.lstrip(" "))
            collected: list[str] = []
            # 如果 value 不是空也不是 JSON / list, 把它当首行
            if value_part and not value_part.startswith(("{", "[", "|", ">", "&", "*")):
                collected.append(value_part)
            # 收集后续更缩进的非空行
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                nxt_stripped = nxt.strip()
                if not nxt_stripped:
                    j += 1
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                if nxt_indent <= indent_base:
                    break
                collected.append(nxt_stripped)
                j += 1
            merged = " ".join(collected) if collected else (value_part or "")
            out.append(f"{' ' * indent_base}{key_part}: {merged}")
            i = j
            continue

        # 模式 3: parameters 是 list-of-objects 格式，转换成 JSON Schema
        if stripped == "parameters:":
            indent_base = len(line) - len(line.lstrip(" "))
            # 收集 list 项
            items: list[dict[str, str]] = []
            j = i + 1
            current: dict[str, str] | None = None
            while j < len(lines):
                nxt = lines[j]
                nxt_stripped = nxt.strip()
                if not nxt_stripped:
                    j += 1
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                if nxt_indent <= indent_base:
                    break
                if nxt_stripped.startswith("- "):
                    if current is not None:
                        items.append(current)
                    current = _parse_list_item(nxt_stripped[2:])
                else:
                    if current is not None:
                        _merge_into(current, nxt_stripped)
                j += 1
            if current is not None:
                items.append(current)
            # 转成 JSON Schema
            schema = _items_to_json_schema(items)
            out.append(f"{' ' * indent_base}parameters: {json.dumps(schema, ensure_ascii=False)}")
            i = j
            continue

        out.append(line)
        i += 1
    return "\n".join(out)


def _parse_list_item(text: str) -> dict[str, str]:
    """``name: x`` → ``{'name': 'x'}``."""
    if ":" in text:
        k, _, v = text.partition(":")
        return {k.strip(): v.strip()}
    return {"_text": text}


def _merge_into(d: dict[str, str], text: str) -> None:
    """``type: integer`` → ``d['type'] = 'integer'``."""
    if ":" in text:
        k, _, v = text.partition(":")
        d[k.strip()] = v.strip()
    else:
        d["_text"] = (d.get("_text", "") + " " + text).strip()


def _items_to_json_schema(items: list[dict[str, str]]) -> dict[str, Any]:
    """``[{'name': 'x', 'type': 'integer', 'required': 'true', ...}, ...]``
    → ``{"type": "object", "properties": {...}, "required": [...]}``"""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for it in items:
        name = it.get("name") or it.get("_text", "")
        if not name or name == "_text":
            continue
        typ = it.get("type", "string")
        # YAML 标量映射到 JSON Schema 类型
        json_type = {
            "integer": "integer",
            "number": "number",
            "boolean": "boolean",
            "string": "string",
            "array": "array",
            "object": "object",
        }.get(typ, "string")
        prop: dict[str, Any] = {"type": json_type}
        if "description" in it:
            prop["description"] = it["description"]
        if "default" in it:
            prop["default"] = _parse_scalar(it["default"])
        properties[name] = prop
        if it.get("required", "").lower() in ("true", "yes", "1"):
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _parse_scalar(text: str) -> Any:
    """把 YAML 标量转 Python 值.

    优先级：null > bool > int > float > JSON > 单行 list > 字符串。
    """
    # null
    if text in ("null", "~", ""):
        return None
    # bool（注意大小写敏感）
    if text in ("true", "True", "yes"):
        return True
    if text in ("false", "False", "no"):
        return False
    # 数字
    try:
        if "." in text or "e" in text or "E" in text:
            return float(text)
        return int(text)
    except ValueError:  # noqa: BLE001 — not a number; fall through to next parse strategy
        pass
    # JSON 字面量（parameters 字段是对象）
    if text.startswith(("{", "[")):
        try:
            return json.loads(text)
        except json.JSONDecodeError:  # noqa: BLE001 — not valid JSON; fall through to next parse strategy
            pass
    # 单行 list：``[a, b, c]`` 或 ``["a", "b"]``
    if text.startswith("[") and text.endswith("]"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:  # noqa: BLE001 — not valid JSON list; fall through to quote-strip
            pass
    # 去掉两端的引号
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        return text[1:-1]
    return text
