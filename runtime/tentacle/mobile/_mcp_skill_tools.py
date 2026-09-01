"""SKILL.md 解析与 MCP tool 定义构建（从 mcp_server 拆分）.

包含：
- SKILL.md frontmatter 解析（``_find_skills_root`` / ``_parse_skill_md`` /
  ``_parse_parameters_block``）
- SKILL.md → MCP tool 定义转换（``skill_to_mcp_tool`` / ``load_all_skill_tools``）
- 管理类 MCP tool 定义（``_build_management_tools``）
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── SKILL.md 解析 ──────────────────────────────────────────────


def _find_skills_roots() -> list[Path]:
    """定位所有 SKILL.md 根目录（mobile + ios）.

    返回顺序固定：先 mobile，再 ios。每个候选根都按
    ``相对本文件 → 相对当前工作目录`` 的顺序探测。
    """
    here = Path(__file__).resolve().parent
    pairs = [
        (here / "skills", Path.cwd() / "runtime" / "tentacle" / "mobile" / "skills"),
        (here.parent / "ios" / "skills", Path.cwd() / "runtime" / "tentacle" / "ios" / "skills"),
    ]
    roots: list[Path] = []
    for local, cwd in pairs:
        if local.is_dir():
            roots.append(local)
        elif cwd.is_dir():
            roots.append(cwd)
    return roots


def _find_skills_root() -> Path:
    """定位 runtime/tentacle/mobile/skills 目录（向后兼容）."""
    roots = _find_skills_roots()
    return roots[0] if roots else Path(__file__).resolve().parent / "skills"


def _parse_skill_md(skill_md_path: Path) -> dict[str, Any] | None:
    """解析 SKILL.md 的 YAML frontmatter.

    返回::

        {
            "name": "android.tap",
            "description": "点击屏幕坐标 (x, y)",
            "affinity": ["mobile", "gui"],
            "parameters": [
                {"name": "x", "type": "integer", "required": true, ...},
                ...
            ]
        }

    解析失败返回 None.
    """
    try:
        text = skill_md_path.read_text(encoding="utf-8")
    except OSError:
        return None

    # 提取 YAML frontmatter（--- 之间的内容）
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return None

    yaml_text = match.group(1)

    # 最小 YAML 解析（不依赖 PyYAML）
    # 只需解析简单的 key: value 和 key: | 多行值
    data: dict[str, Any] = {}
    lines = yaml_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        # 跳过空行和注释
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue

        # 匹配 key: value 或 key: |
        m = re.match(r"^(\w[\w-]*):\s*(.*)", line)
        if not m:
            i += 1
            continue

        key = m.group(1)
        value = m.group(2).strip()

        if value == "|":
            # 多行值，读取后续缩进行
            multiline_lines: list[str] = []
            i += 1
            while i < len(lines):
                if lines[i].strip() and not lines[i].startswith(" "):
                    break
                multiline_lines.append(lines[i].rstrip())
                i += 1
            data[key] = "\n".join(multiline_lines).strip()
            continue

        if value.startswith("[") and value.endswith("]"):
            # 简单列表，如 [mobile, gui]
            items = [item.strip().strip("'\"") for item in value[1:-1].split(",")]
            data[key] = items
            i += 1
            continue

        data[key] = value
        i += 1

    # 解析 parameters（列表块）
    if "parameters" in yaml_text:
        data["parameters"] = _parse_parameters_block(yaml_text)

    if "name" not in data:
        return None

    return data


def _parse_parameters_block(yaml_text: str) -> list[dict[str, Any]]:
    """解析 YAML 中的 parameters 列表块.

    格式::

        parameters:
          - name: x
            type: integer
            required: true
            description: X coordinate
          - name: y
            type: integer
            required: true
            description: Y coordinate
    """
    params: list[dict[str, Any]] = []
    lines = yaml_text.split("\n")

    # 找到 parameters: 行
    in_params = False
    current_param: dict[str, Any] | None = None

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("parameters:"):
            in_params = True
            continue

        if not in_params:
            continue

        # 检查是否离开了 parameters 块（非空且不缩进）
        if stripped and not line.startswith(" "):
            break

        # 新参数项（- name: xxx）
        if stripped.startswith("- "):
            if current_param is not None:
                params.append(current_param)
            current_param = {}
            # 解析 "- name: xxx" 或 "- name: |"
            rest = stripped[2:].strip()
            m = re.match(r"(\w+):\s*(.*)", rest)
            if m:
                k, v = m.group(1), m.group(2).strip()
                if v == "|":
                    # 多行值暂不处理，设为空
                    current_param[k] = ""
                else:
                    current_param[k] = v
            continue

        # 参数属性（缩进的 key: value）
        if current_param is not None and stripped:
            m = re.match(r"(\w+):\s*(.*)", stripped)
            if m:
                k, v = m.group(1), m.group(2).strip()
                # 类型转换
                if k == "required":
                    current_param[k] = v.lower() in ("true", "yes")
                elif k in ("default",) and v:
                    # 尝试数字转换
                    try:
                        current_param[k] = int(v)
                    except ValueError:
                        try:
                            current_param[k] = float(v)
                        except ValueError:
                            current_param[k] = v
                else:
                    current_param[k] = v

    if current_param is not None:
        params.append(current_param)

    return params


# ── SKILL.md → MCP Tool 定义转换 ──────────────────────────────

# SKILL.md 中的 type → JSON Schema type 映射
_TYPE_MAP = {
    "integer": "integer",
    "int": "integer",
    "number": "number",
    "float": "number",
    "string": "string",
    "str": "string",
    "boolean": "boolean",
    "bool": "boolean",
    "array": "array",
    "object": "object",
}


def skill_to_mcp_tool(skill_data: dict[str, Any]) -> dict[str, Any]:
    """将 SKILL.md 解析数据转换为 MCP tool 定义.

    SKILL.md frontmatter::

        name: android.tap
        description: 点击屏幕坐标 (x, y)
        parameters:
          - name: x
            type: integer
            required: true

    MCP tool 定义::

        name: "android_tap"
        description: "点击屏幕坐标 (x, y)"
        inputSchema:
          type: object
          properties:
            x: {type: integer}
            y: {type: integer}
          required: ["x", "y"]
    """
    skill_name = skill_data.get("name", "")
    # MCP tool name 不能包含 `.`，转为 `_`
    mcp_name = skill_name.replace(".", "_")

    description = skill_data.get("description", "")
    # 清理多行描述
    if isinstance(description, str):
        description = " ".join(description.split())

    # 构建 inputSchema
    properties: dict[str, Any] = {}
    required: list[str] = []
    raw_params = skill_data.get("parameters", [])

    for param in raw_params:
        pname = param.get("name", "")
        if not pname:
            continue

        ptype = _TYPE_MAP.get(param.get("type", "string"), "string")
        pdesc = param.get("description", "")
        if isinstance(pdesc, str):
            pdesc = " ".join(pdesc.split())

        prop: dict[str, Any] = {"type": ptype}
        if pdesc:
            prop["description"] = pdesc

        # enum 约束
        enum_values = param.get("enum")
        if enum_values:
            if isinstance(enum_values, str):
                prop["enum"] = [v.strip() for v in enum_values.split(",")]
            elif isinstance(enum_values, list):
                prop["enum"] = enum_values

        # default 值
        default_val = param.get("default")
        if default_val is not None:
            prop["default"] = default_val

        properties[pname] = prop

        if param.get("required", False):
            required.append(pname)

    # 所有 MCP tool 自动添加可选的 _tentacle_id 参数
    properties["_tentacle_id"] = {
        "type": "string",
        "description": "目标设备 ID（可选，不指定则自动选择第一个在线设备）",
    }

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        input_schema["required"] = required

    tool: dict[str, Any] = {
        "name": mcp_name,
        "description": description,
        "inputSchema": input_schema,
        # 在 _meta 中保留原始 skill name
        "_meta": {"skill_name": skill_name},
    }

    return tool


def load_all_skill_tools(skills_root: Path | None = None) -> list[dict[str, Any]]:
    """加载所有 SKILL.md 并转换为 MCP tool 定义.

    Args:
        skills_root: 单个 SKILL.md 根目录路径；None 时自动加载 mobile + ios 两个根目录

    Returns:
        MCP tool 定义列表（按名称去重，避免跨根重复）
    """
    roots = [skills_root] if skills_root is not None else _find_skills_roots()

    tools: list[dict[str, Any]] = []
    seen: set[str] = set()

    for root in roots:
        if not root.is_dir():
            logger.warning("skills root not found: %s", root)
            continue

        for skill_dir in sorted(root.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            skill_data = _parse_skill_md(skill_md)
            if skill_data is None:
                logger.warning("failed to parse SKILL.md: %s", skill_md)
                continue

            tool = skill_to_mcp_tool(skill_data)
            name = tool.get("name", "")
            if name in seen:
                continue
            seen.add(name)
            tools.append(tool)

    logger.info("loaded %d skill tools from %d roots", len(tools), len(roots))
    return tools


def _platform_prefix(device: Any) -> str:
    """返回设备对应的技能前缀（``ios`` / ``android``）.

    Args:
        device: 任意带 ``platform`` 属性的设备（IOSDevice / MobileDevice）
    """
    return getattr(device, "platform", "android")


def _screenshot_tool_for(device: Any) -> str:
    """返回设备对应的截图工具名（``ios.take_screenshot`` / ``android.take_screenshot``）."""
    return f"{_platform_prefix(device)}.take_screenshot"


# ── 特殊管理类 MCP tools ──────────────────────────────────────


def _build_management_tools() -> list[dict[str, Any]]:
    """构建管理类 MCP tool 定义.

    除了 30 个手机工具，额外暴露几个管理类工具：
    - list_devices — 列出已连接设备
    - get_device_status — 获取设备详情
    - take_screenshot — 获取设备截图（返回 base64 图片）
    - analyze_screen — VLM 分析屏幕（如果 VLM 已配置）
    """
    return [
        {
            "name": "list_devices",
            "description": "列出所有已连接的移动设备，包括在线/离线状态、平台、能力等信息",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "online_only": {
                        "type": "boolean",
                        "description": "是否只返回在线设备",
                        "default": False,
                    },
                },
            },
            "_meta": {"skill_name": "_management.list_devices"},
        },
        {
            "name": "get_device_status",
            "description": "获取指定设备的详细状态信息，包括能力列表、元数据、电池等",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tentacle_id": {
                        "type": "string",
                        "description": "设备 ID",
                    },
                },
                "required": ["tentacle_id"],
            },
            "_meta": {"skill_name": "_management.get_device_status"},
        },
        {
            "name": "take_screenshot",
            "description": "获取设备当前屏幕截图，返回 base64 编码的 JPEG 图片",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "_tentacle_id": {
                        "type": "string",
                        "description": "目标设备 ID（可选，不指定则自动选择第一个在线设备）",
                    },
                },
            },
            "_meta": {"skill_name": "_management.take_screenshot"},
        },
        {
            "name": "analyze_screen",
            "description": "使用 VLM（视觉语言模型）分析设备当前屏幕，返回屏幕内容描述和操作建议",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "你想在屏幕上执行的任务描述",
                    },
                    "_tentacle_id": {
                        "type": "string",
                        "description": "目标设备 ID（可选，不指定则自动选择第一个在线设备）",
                    },
                },
                "required": ["task"],
            },
            "_meta": {"skill_name": "_management.analyze_screen"},
        },
    ]


__all__ = [
    "load_all_skill_tools",
    "skill_to_mcp_tool",
]
