"""Discoverable PluginHub wrapper for the local-only ComfyUI integration."""

from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter

from runtime.execution.suckers.registry import Skill
from runtime.platform.plugins.plugin_base import ModulePlugin
from runtime.platform.process.paths import app_paths
from runtime.sensing.gateway.comfyui_manager import managed_home, manager_status

from .workflow_diagnostics import diagnose_workflow

_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_SAFE_WORKFLOW_ID = re.compile(r"^[a-zA-Z0-9._-]{1,80}$")
_MODEL_GROUPS = ("checkpoints", "diffusion_models", "loras", "vae", "controlnet")
_MODEL_SUFFIXES = frozenset({".safetensors", ".ckpt", ".pt", ".pth", ".bin"})


class ComfyUIBridgePlugin(ModulePlugin):
    name = "comfyui_bridge"
    display_name = "ComfyUI 工作流桥接"
    version = "0.8.0"
    description = (
        "ComfyUI 托管安装、节点扩展回滚、逐项授权模型管理、本机工作流编辑、持久化与队列接入"
    )
    author = "Echo"

    def _local_base_url(self) -> str | None:
        if self.ctx is None:
            return None
        base_url = str(self.ctx.config.get("base_url") or "http://127.0.0.1:8188").rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in _LOOPBACK:
            return None
        return base_url

    def register_skills(self) -> None:
        if self.ctx is None:
            return
        skills = [
            Skill(
                name="comfyui_bridge.status",
                description="探测本机 ComfyUI 是否在线并返回 system_stats。不访问远程地址。",
                summary="检查本机 ComfyUI 状态",
                affinity=["design", "image", "video", "comfyui", "read"],
                cost_profile="low",
                trusted_source="plugin://comfyui_bridge",
                handler=self._status_skill,
            ),
            Skill(
                name="comfyui_bridge.dependencies",
                description=(
                    "只读盘点本机 ComfyUI 的模型和 custom_nodes 扩展。"
                    "返回 checkpoints、LoRA、VAE、ControlNet 等数量，不下载或修改文件。"
                ),
                summary="盘点本机 ComfyUI 模型与扩展",
                affinity=["design", "image", "video", "comfyui", "dependencies", "read"],
                cost_profile="low",
                trusted_source="plugin://comfyui_bridge",
                handler=self._dependencies_skill,
            ),
            Skill(
                name="comfyui_bridge.manager_status",
                description=(
                    "只读查看 Echo 托管 ComfyUI 的安装、版本与后台任务状态。"
                    "安装和更新必须由用户在 Design 界面主动点击，Agent 不得自行触发。"
                ),
                summary="查看托管 ComfyUI 安装与版本状态",
                affinity=["design", "comfyui", "dependencies", "read"],
                cost_profile="low",
                trusted_source="plugin://comfyui_bridge",
                handler=lambda **_kwargs: {"ok": True, **manager_status()},
            ),
            Skill(
                name="comfyui_bridge.workflows",
                description="列出 Echo 已内置和用户已导入的 ComfyUI 工作流；不执行工作流。",
                summary="列出 ComfyUI 工作流",
                affinity=["design", "image", "video", "comfyui", "workflow", "read"],
                cost_profile="low",
                trusted_source="plugin://comfyui_bridge",
                handler=self._workflows_skill,
            ),
            Skill(
                name="comfyui_bridge.workflow_get",
                description=(
                    "读取一份已内置或用户已导入的 ComfyUI API 工作流完整 JSON。"
                    "参数 workflow_id 必填；返回的 workflow 可直接交给 queue。"
                ),
                summary="读取 ComfyUI 工作流(workflow_id 必填)",
                affinity=["design", "image", "video", "comfyui", "workflow", "read"],
                cost_profile="low",
                trusted_source="plugin://comfyui_bridge",
                handler=self._workflow_get_skill,
            ),
            Skill(
                name="comfyui_bridge.workflow_diagnostics",
                description=(
                    "核对一份已保存工作流与本机 ComfyUI 的真实兼容性。参数 workflow_id "
                    "必填；检查缺失节点、缺失必填输入、无效枚举、未知输入与缺失模型文件，"
                    "只读且不会安装依赖。"
                ),
                summary="诊断 ComfyUI 工作流依赖(workflow_id 必填)",
                affinity=["design", "comfyui", "workflow", "diagnostics", "read"],
                cost_profile="low",
                trusted_source="plugin://comfyui_bridge",
                handler=self._workflow_diagnostics_skill,
            ),
            Skill(
                name="comfyui_bridge.workflow_save",
                description=(
                    "新建或更新用户 ComfyUI API 工作流。参数 workflow_id、name、workflow "
                    "必填，ui 和 expected_revision 可选；以 revision 阻止静默覆盖。"
                ),
                summary="保存 ComfyUI 工作流并检查版本",
                affinity=["design", "image", "video", "comfyui", "workflow", "write"],
                cost_profile="low",
                trusted_source="plugin://comfyui_bridge",
                handler=self._workflow_save_skill,
            ),
            Skill(
                name="comfyui_bridge.queue",
                description=(
                    "把 ComfyUI API 格式的 prompt 图提交到本机 ComfyUI 队列。参数 prompt "
                    "必填，client_id 可选。只有用户明确要求生成/运行工作流时才能调用；"
                    "调用会真实占用本机模型与算力。"
                ),
                summary="提交本机 ComfyUI 工作流(prompt 必填)",
                affinity=["design", "image", "video", "comfyui", "workflow", "write"],
                cost_profile="high",
                trusted_source="plugin://comfyui_bridge",
                handler=self._queue_skill,
                timeout_s=20,
            ),
            Skill(
                name="comfyui_bridge.result",
                description=(
                    "查询本机 ComfyUI 队列任务的运行状态和输出文件。参数 prompt_id 必填。"
                    "未完成时返回 pending；完成后返回图片/视频/音频输出及本机预览 URL。"
                ),
                summary="查询 ComfyUI 生成结果(prompt_id 必填)",
                affinity=["design", "image", "video", "comfyui", "result", "read"],
                cost_profile="low",
                trusted_source="plugin://comfyui_bridge",
                handler=self._result_skill,
            ),
        ]
        for skill in skills:
            with contextlib.suppress(Exception):
                self.ctx.register_skill(skill)

    def _status_skill(self, **_kwargs: Any) -> dict[str, Any]:
        base_url = self._local_base_url()
        if base_url is None:
            return {"ok": False, "online": False, "error": "ComfyUI 地址必须是本机地址"}
        try:
            response = httpx.get(f"{base_url}/system_stats", timeout=2)
            response.raise_for_status()
            return {"ok": True, "online": True, "baseUrl": base_url, "system": response.json()}
        except (httpx.HTTPError, ValueError) as exc:
            return {"ok": False, "online": False, "baseUrl": base_url, "error": str(exc)}

    def _dependencies_skill(self, **_kwargs: Any) -> dict[str, Any]:
        configured = os.environ.get("ECHO_COMFYUI_HOME", "").strip()
        candidates = [
            Path(configured).expanduser() if configured else None,
            managed_home(),
            app_paths().data_dir / "comfyui",
            Path.home() / "ComfyUI",
        ]
        home = next(
            (candidate.resolve() for candidate in candidates if candidate and candidate.is_dir()),
            None,
        )
        model_counts = {group: 0 for group in _MODEL_GROUPS}
        custom_nodes: list[str] = []
        if home is not None:
            for group in _MODEL_GROUPS:
                directory = home / "models" / group
                if directory.is_dir():
                    model_counts[group] = sum(
                        1
                        for path in directory.rglob("*")
                        if path.is_file() and path.suffix.lower() in _MODEL_SUFFIXES
                    )
            nodes_dir = home / "custom_nodes"
            if nodes_dir.is_dir():
                custom_nodes = sorted(
                    path.name
                    for path in nodes_dir.iterdir()
                    if path.is_dir() and not path.name.startswith((".", "_"))
                )[:200]
        return {
            "ok": True,
            "detected": home is not None,
            "configured": bool(configured),
            "path": str(home) if home is not None else None,
            "modelCounts": model_counts,
            "totalModels": sum(model_counts.values()),
            "customNodes": custom_nodes,
            "totalCustomNodes": len(custom_nodes),
            "managed": home == managed_home() and manager_status().get("installed") is True,
            "manager": manager_status(),
        }

    def _workflows_skill(self, **_kwargs: Any) -> dict[str, Any]:
        if self.ctx is None:
            return {"ok": False, "items": [], "error": "plugin is not loaded"}
        directories = [
            ("bundled", Path(self.ctx.plugin_dir) / "workflows"),
            ("user", app_paths().data_dir / "design" / "comfyui-workflows"),
        ]
        indexed: dict[str, dict[str, Any]] = {}
        for source, directory in directories:
            for path in sorted(directory.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict) or not isinstance(payload.get("workflow"), dict):
                    continue
                indexed[path.stem] = {
                    "id": path.stem,
                    "name": payload.get("name", path.stem),
                    "description": payload.get("description", ""),
                    "tags": payload.get("tags", []),
                    "source": source,
                }
        items = list(indexed.values())
        return {"ok": True, "items": items, "total": len(items)}

    def _workflow_directories(self) -> list[tuple[str, Path]]:
        if self.ctx is None:
            return []
        return [
            ("user", app_paths().data_dir / "design" / "comfyui-workflows"),
            ("bundled", Path(self.ctx.plugin_dir) / "workflows"),
        ]

    def _workflow_get_skill(self, workflow_id: str = "", **_kwargs: Any) -> dict[str, Any]:
        if not _SAFE_WORKFLOW_ID.fullmatch(workflow_id):
            return {"ok": False, "error": "invalid workflow_id"}
        for source, directory in self._workflow_directories():
            path = directory / f"{workflow_id}.json"
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, json.JSONDecodeError) as exc:
                return {"ok": False, "error": str(exc)}
            if isinstance(payload, dict) and isinstance(payload.get("workflow"), dict):
                return {"ok": True, "id": workflow_id, "source": source, **payload}
        return {"ok": False, "error": "workflow not found"}

    def _workflow_save_skill(
        self,
        workflow_id: str = "",
        name: str = "",
        workflow: dict[str, Any] | None = None,
        ui: dict[str, Any] | None = None,
        expected_revision: int = 0,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not _SAFE_WORKFLOW_ID.fullmatch(workflow_id):
            return {"ok": False, "error": "invalid workflow_id"}
        if not name.strip() or not isinstance(workflow, dict):
            return {"ok": False, "error": "name and workflow are required"}
        target_dir = app_paths().data_dir / "design" / "comfyui-workflows"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{workflow_id}.json"
        current_revision = 0
        try:
            current = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(current, dict):
                current_revision = int(current.get("revision") or 0)
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        if expected_revision != current_revision:
            return {
                "ok": False,
                "code": "WORKFLOW_REVISION_CONFLICT",
                "revision": current_revision,
            }
        revision = current_revision + 1
        payload = {
            "name": name.strip()[:120],
            "workflow": workflow,
            "ui": ui if isinstance(ui, dict) else {},
            "revision": revision,
        }
        temporary = target.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(target)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "id": workflow_id, **payload}

    def _workflow_diagnostics_skill(self, workflow_id: str = "", **_kwargs: Any) -> dict[str, Any]:
        loaded = self._workflow_get_skill(workflow_id=workflow_id)
        if not loaded.get("ok"):
            return loaded
        object_info: dict[str, Any] | None = None
        catalog_error: str | None = None
        base_url = self._local_base_url()
        if base_url is not None:
            try:
                response = httpx.get(f"{base_url}/object_info", timeout=4)
                response.raise_for_status()
                candidate = response.json()
                if isinstance(candidate, dict):
                    object_info = candidate
                else:
                    catalog_error = "local ComfyUI returned an invalid node catalog"
            except (httpx.HTTPError, ValueError) as exc:
                catalog_error = str(exc)
        dependencies = self._dependencies_skill()
        home = Path(dependencies["path"]) if dependencies.get("path") else None
        result = diagnose_workflow(
            loaded["workflow"],
            object_info=object_info,
            comfy_home=home,
        )
        return {
            "workflowId": workflow_id,
            "source": loaded.get("source"),
            "nodeCatalogError": catalog_error,
            **result,
        }

    def _queue_skill(
        self,
        prompt: dict[str, Any] | None = None,
        workflow_id: str | None = None,
        client_id: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        base_url = self._local_base_url()
        if base_url is None:
            return {"ok": False, "error": "ComfyUI 地址必须是本机地址"}
        if (not isinstance(prompt, dict) or not prompt) and workflow_id:
            loaded = self._workflow_get_skill(workflow_id=workflow_id)
            prompt = loaded.get("workflow") if loaded.get("ok") else None
            if prompt is None:
                return loaded
        if not isinstance(prompt, dict) or not prompt:
            return {"ok": False, "error": "prompt or workflow_id is required"}
        payload: dict[str, Any] = {"prompt": prompt}
        if client_id:
            payload["client_id"] = str(client_id)[:120]
        try:
            response = httpx.post(f"{base_url}/prompt", json=payload, timeout=15)
            response.raise_for_status()
            result = response.json()
            return (
                {"ok": True, **result}
                if isinstance(result, dict)
                else {"ok": True, "result": result}
            )
        except (httpx.HTTPError, ValueError) as exc:
            return {"ok": False, "error": f"ComfyUI is unavailable: {exc}"}

    def _result_skill(self, prompt_id: str = "", **_kwargs: Any) -> dict[str, Any]:
        base_url = self._local_base_url()
        if base_url is None:
            return {"ok": False, "error": "ComfyUI 地址必须是本机地址"}
        if not prompt_id or len(prompt_id) > 160:
            return {"ok": False, "error": "prompt_id is required"}
        try:
            response = httpx.get(f"{base_url}/history/{prompt_id}", timeout=5)
            response.raise_for_status()
            history = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {"ok": False, "error": f"ComfyUI is unavailable: {exc}"}
        record = history.get(prompt_id) if isinstance(history, dict) else None
        if not isinstance(record, dict):
            return {"ok": True, "state": "pending", "promptId": prompt_id, "outputs": []}
        outputs: list[dict[str, Any]] = []
        for node_id, node_output in (record.get("outputs") or {}).items():
            if not isinstance(node_output, dict):
                continue
            for media_type, values in node_output.items():
                if not isinstance(values, list):
                    continue
                for item in values:
                    if not isinstance(item, dict) or not item.get("filename"):
                        continue
                    query = httpx.QueryParams(
                        {
                            "filename": str(item["filename"]),
                            "subfolder": str(item.get("subfolder") or ""),
                            "type": str(item.get("type") or "output"),
                        }
                    )
                    outputs.append(
                        {
                            "nodeId": str(node_id),
                            "kind": media_type,
                            **item,
                            "url": f"{base_url}/view?{query}",
                        }
                    )
        status = record.get("status") if isinstance(record.get("status"), dict) else {}
        completed = bool(status.get("completed", outputs))
        return {
            "ok": True,
            "state": "completed" if completed else "running",
            "promptId": prompt_id,
            "outputs": outputs,
            "status": status,
        }

    def register_routes(self) -> None:
        if self.ctx is None or self.ctx.fastapi_app is None:
            return
        router = APIRouter(prefix="/api/plugins/comfyui-bridge", tags=["comfyui_bridge"])

        @router.get("/health")
        def health() -> dict[str, Any]:
            base_url = self._local_base_url()
            local_only = base_url is not None
            return {
                "ok": local_only,
                "plugin": self.name,
                "local_only": local_only,
                "base_url": base_url,
                "integration_api": "/api/design/comfyui",
            }

        @router.get("/capabilities")
        def capabilities() -> dict[str, Any]:
            return {
                "status": "/api/design/comfyui/status",
                "dependencies": "/api/design/comfyui/dependencies",
                "start": "/api/design/comfyui/start",
                "stop": "/api/design/comfyui/stop",
                "manager": "/api/design/comfyui/manager",
                "install": "/api/design/comfyui/install",
                "update": "/api/design/comfyui/update",
                "cancel_install": "/api/design/comfyui/manager/cancel",
                "custom_node_registry": "/api/design/comfyui/custom-nodes/registry",
                "custom_node_install": "/api/design/comfyui/custom-nodes/install",
                "custom_node_update": "/api/design/comfyui/custom-nodes/update",
                "custom_node_uninstall": "/api/design/comfyui/custom-nodes/{node_id}",
                "custom_node_rollback": ("/api/design/comfyui/custom-nodes/{node_id}/rollback"),
                "models": "/api/design/comfyui/models",
                "model_download": "/api/design/comfyui/models/download",
                "model_remove": "/api/design/comfyui/models/remove",
                "model_restore": "/api/design/comfyui/models/restore",
                "node_catalog": "/api/design/comfyui/object-info",
                "workflows": "/api/design/comfyui/workflows",
                "import_workflow": "/api/design/comfyui/workflows/import",
                "save_workflow": "/api/design/comfyui/workflows/{workflow_id}",
                "workflow_diagnostics": ("/api/design/comfyui/workflows/{workflow_id}/diagnostics"),
                "queue": "/api/design/comfyui/queue",
                "result": "/api/design/comfyui/history/{prompt_id}",
                "embedded_surface": True,
            }

        self.ctx.fastapi_app.include_router(router)


__all__ = ["ComfyUIBridgePlugin"]
