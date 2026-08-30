"""Compatibility diagnostics for ComfyUI API-format workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

MODEL_INPUT_GROUPS: dict[str, tuple[str, ...]] = {
    "ckpt_name": ("checkpoints", "diffusion_models"),
    "checkpoint": ("checkpoints", "diffusion_models"),
    "vae_name": ("vae",),
    "lora_name": ("loras",),
    "control_net_name": ("controlnet",),
    "controlnet_name": ("controlnet",),
    "clip_name": ("text_encoders",),
    "clip_vision": ("clip_vision",),
    "model_name": ("upscale_models",),
}
MODEL_SUFFIXES = frozenset({".safetensors", ".ckpt", ".pt", ".pth", ".bin"})


def diagnose_workflow(
    workflow: dict[str, Any],
    *,
    object_info: dict[str, Any] | None,
    comfy_home: Path | None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    node_types: set[str] = set()
    model_refs: list[dict[str, Any]] = []
    installed = _installed_models(comfy_home)

    if object_info is None:
        issues.append(
            {
                "kind": "node_catalog_unavailable",
                "severity": "warning",
                "detail": "ComfyUI 离线，无法核对节点类型与输入规格",
            }
        )
    if comfy_home is None:
        issues.append(
            {
                "kind": "comfyui_home_missing",
                "severity": "warning",
                "detail": "未检测到 ComfyUI 目录，无法核对本地模型文件",
            }
        )

    for node_id, raw_node in workflow.items():
        if not isinstance(raw_node, dict):
            issues.append(
                {
                    "kind": "invalid_node",
                    "severity": "error",
                    "nodeId": str(node_id),
                    "detail": "节点不是对象",
                }
            )
            continue
        class_type = str(raw_node.get("class_type") or "")
        inputs = raw_node.get("inputs") if isinstance(raw_node.get("inputs"), dict) else {}
        if not class_type:
            issues.append(
                {
                    "kind": "missing_class_type",
                    "severity": "error",
                    "nodeId": str(node_id),
                    "detail": "节点缺少 class_type",
                }
            )
            continue
        node_types.add(class_type)
        spec = object_info.get(class_type) if object_info is not None else None
        if object_info is not None and not isinstance(spec, dict):
            issues.append(
                {
                    "kind": "missing_node_type",
                    "severity": "error",
                    "nodeId": str(node_id),
                    "classType": class_type,
                    "detail": f"本机未安装节点 {class_type}",
                }
            )
        elif isinstance(spec, dict):
            _check_inputs(str(node_id), class_type, inputs, spec, issues)

        for input_name, groups in MODEL_INPUT_GROUPS.items():
            value = inputs.get(input_name)
            if not isinstance(value, str) or not value.strip():
                continue
            reference = {
                "nodeId": str(node_id),
                "classType": class_type,
                "input": input_name,
                "value": value,
                "groups": list(groups),
            }
            model_refs.append(reference)
            if comfy_home is not None and not _model_exists(value, groups, installed):
                issues.append(
                    {
                        "kind": "missing_model",
                        "severity": "error",
                        **reference,
                        "detail": f"本机找不到模型文件 {value}",
                    }
                )

    errors = sum(item["severity"] == "error" for item in issues)
    warnings = len(issues) - errors
    fully_checked = object_info is not None and comfy_home is not None
    return {
        "ok": True,
        "compatible": fully_checked and errors == 0,
        "fullyChecked": fully_checked,
        "counts": {
            "nodes": len(workflow),
            "nodeTypes": len(node_types),
            "modelReferences": len(model_refs),
            "errors": errors,
            "warnings": warnings,
        },
        "nodeTypes": sorted(node_types),
        "modelReferences": model_refs,
        "issues": issues,
    }


def _check_inputs(
    node_id: str,
    class_type: str,
    inputs: dict[str, Any],
    spec: dict[str, Any],
    issues: list[dict[str, Any]],
) -> None:
    groups = spec.get("input") if isinstance(spec.get("input"), dict) else {}
    definitions: dict[str, tuple[Any, bool]] = {}
    for group_name in ("required", "optional"):
        raw = groups.get(group_name)
        if isinstance(raw, dict):
            definitions.update(
                {str(name): (value, group_name == "required") for name, value in raw.items()}
            )
    for name, (input_spec, required) in definitions.items():
        if required and name not in inputs:
            issues.append(
                {
                    "kind": "missing_required_input",
                    "severity": "error",
                    "nodeId": node_id,
                    "classType": class_type,
                    "input": name,
                    "detail": f"{class_type} 缺少必填输入 {name}",
                }
            )
            continue
        if name not in inputs or not isinstance(input_spec, list) or not input_spec:
            continue
        choices = input_spec[0]
        value = inputs[name]
        if isinstance(choices, list) and isinstance(value, str) and value not in choices:
            issues.append(
                {
                    "kind": "invalid_enum_value",
                    "severity": "error",
                    "nodeId": node_id,
                    "classType": class_type,
                    "input": name,
                    "value": value,
                    "detail": f"{name} 的值不在本机节点允许范围内",
                }
            )
    for name in inputs:
        if name not in definitions:
            issues.append(
                {
                    "kind": "unknown_input",
                    "severity": "warning",
                    "nodeId": node_id,
                    "classType": class_type,
                    "input": str(name),
                    "detail": f"本机 {class_type} 不识别输入 {name}",
                }
            )


def _installed_models(home: Path | None) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    if home is None:
        return result
    models = home / "models"
    for groups in MODEL_INPUT_GROUPS.values():
        for group in groups:
            if group in result:
                continue
            directory = models / group
            names: set[str] = set()
            if directory.is_dir():
                for index, path in enumerate(directory.rglob("*")):
                    if index >= 5000:
                        break
                    if path.is_file() and path.suffix.lower() in MODEL_SUFFIXES:
                        relative = path.relative_to(directory).as_posix().lower()
                        names.update({relative, path.name.lower()})
            result[group] = names
    return result


def _model_exists(value: str, groups: tuple[str, ...], installed: dict[str, set[str]]) -> bool:
    normalized = value.replace("\\", "/").lstrip("./").lower()
    basename = Path(normalized).name
    return any(
        normalized in installed.get(group, set()) or basename in installed.get(group, set())
        for group in groups
    )


__all__ = ["diagnose_workflow"]
