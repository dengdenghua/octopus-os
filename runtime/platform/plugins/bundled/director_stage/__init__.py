"""Echo original persistent 3D director-stage plugin."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from runtime.execution.suckers.registry import Skill
from runtime.platform.plugins.plugin_base import ModulePlugin
from runtime.platform.process.paths import app_paths

from .model_renderer import capture_model, compare_model
from .scene_snapshot import read_visual_snapshot, save_visual_snapshot
from .scene_store import (
    PROP_CATALOG,
    diagnose_scene,
    edit_scene,
    load_scene,
    read_camera_path,
    read_motion,
    save_scene,
    scene_view,
    step_history,
)

_POSES = [
    "stand",
    "tpose",
    "walk",
    "run",
    "jump",
    "sit",
    "squat",
    "kneel",
    "lie",
    "drive",
    "wave",
    "hands_up",
    "bow",
    "akimbo",
    "think",
    "fight",
    "aim",
    "sword",
    "spell",
]
_SCENE_SCHEMA: dict[str, Any] = {
    "version": 1,
    "coordinate_system": "right_handed_y_up",
    "entities": {
        "scene": ["scale", "position", "rotation", "skyColor", "background"],
        "camera": ["position", "target", "focalLength", "aspectRatio"],
        "character": ["position", "rotation", "scale", "bodyType", "pose"],
        "prop": ["assetId", "position", "rotation", "scale", "shape", "size", "color"],
        "model": ["position", "rotation", "scale", "parts", "bbox"],
    },
    "model_primitives": ["box", "sphere", "cylinder", "cone"],
    "prop_catalog": list(PROP_CATALOG),
    "timeline_tracks": ["camera_path", "object_path", "character_animation", "phone_camera"],
    "poses": _POSES,
}


class SceneEditBody(BaseModel):
    operations: list[dict[str, Any]] = Field(min_length=1, max_length=50)
    validate_only: bool = False


class SceneHistoryBody(BaseModel):
    action: str
    steps: int = Field(default=1, ge=1, le=20)


class ModelGenerateBody(BaseModel):
    model_id: str | None = None
    label: str = Field(default="程序化模型", max_length=40)
    position: list[float] = Field(default_factory=lambda: [0, 0, 0], min_length=3, max_length=3)
    rotation: list[float] = Field(default_factory=lambda: [0, 0, 0], min_length=3, max_length=3)
    scale: list[float] = Field(default_factory=lambda: [1, 1, 1], min_length=3, max_length=3)
    parts: list[dict[str, Any]] = Field(min_length=1, max_length=64)


class ModelCaptureBody(BaseModel):
    views: list[str] | None = Field(default=None, min_length=1, max_length=4)
    max_dim: int = Field(default=640, ge=240, le=1280)


class ModelCompareBody(BaseModel):
    reference_path: str
    view: str = "iso"


class VisualSnapshotBody(BaseModel):
    data_url: str
    view: str = Field(default="director", pattern="^(director|camera)$")


def _scenes_dir() -> Path:
    target = app_paths().data_dir / "design" / "director-scenes"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _model_output_dir(scene_id: str) -> Path:
    target = app_paths().data_dir / "design" / "director-model-captures" / scene_id
    target.mkdir(parents=True, exist_ok=True)
    return target


def _scene_snapshot_dir(scene_id: str) -> Path:
    return app_paths().data_dir / "design" / "director-scene-snapshots" / scene_id


class DirectorStagePlugin(ModulePlugin):
    name = "director_stage"
    display_name = "3D 导演台"
    version = "0.6.0"
    description = "本地角色、场景、相机、安全声明式模型、真实画面快照与时间线预演工作台"
    author = "Echo"

    def register_skills(self) -> None:
        if self.ctx is None:
            return
        skills = [
            Skill(
                name="director_stage.scene_get",
                description=(
                    "读取 3D 导演台场景。参数 scene_id 必填；view 可选 summary/entities/"
                    "timeline/full。返回角色、相机、姿态和时间线路径。"
                ),
                summary="读取导演台场景(scene_id 必填)",
                affinity=["design", "3d", "director", "scene", "read"],
                cost_profile="low",
                trusted_source="plugin://director_stage",
                handler=self._scene_get_skill,
            ),
            Skill(
                name="director_stage.scene_edit",
                description=(
                    "原子批量编辑 3D 导演台场景。参数 scene_id 与 operations 必填；支持"
                    "场景属性、角色/相机/道具、19 种姿势、空间变换、对象路径和相机路径。"
                    "任一步失败整体回滚；"
                    "validate_only=true 仅验证。"
                ),
                summary="原子编辑导演台场景(scene_id+operations)",
                affinity=["design", "3d", "director", "scene", "write"],
                cost_profile="mid",
                trusted_source="plugin://director_stage",
                handler=self._scene_edit_skill,
            ),
            Skill(
                name="director_stage.scene_diagnostics",
                description="检查导演台场景是否缺相机、缺角色、实体 ID 重复或时间线引用失效。",
                summary="诊断导演台场景(scene_id 必填)",
                affinity=["design", "3d", "director", "diagnostics", "read"],
                cost_profile="low",
                trusted_source="plugin://director_stage",
                handler=self._scene_diagnostics_skill,
            ),
            Skill(
                name="director_stage.scene_history",
                description=(
                    "撤销或重做导演台场景编辑。参数 scene_id、action=undo|redo；steps 可选 1-20。"
                ),
                summary="导演台场景撤销/重做(scene_id+action)",
                affinity=["design", "3d", "director", "history", "write"],
                cost_profile="low",
                trusted_source="plugin://director_stage",
                handler=self._scene_history_skill,
            ),
            Skill(
                name="director_stage.motion_read",
                description="读取内置或自定义角色动作 DSL。参数 scene_id 与 motion_id 必填。",
                summary="读取导演台动作 DSL(scene_id+motion_id)",
                affinity=["design", "3d", "director", "motion", "read"],
                cost_profile="low",
                trusted_source="plugin://director_stage",
                handler=self._motion_read_skill,
            ),
            Skill(
                name="director_stage.campath_read",
                description="读取相机路径的控制点、节奏、循环和 DSL 源。参数 scene_id 与 path_id。",
                summary="读取导演台运镜路径(scene_id+path_id)",
                affinity=["design", "3d", "director", "camera", "read"],
                cost_profile="low",
                trusted_source="plugin://director_stage",
                handler=self._campath_read_skill,
            ),
            Skill(
                name="director_stage.model_generate",
                description=(
                    "安全生成或替换声明式 3D 模型。参数 scene_id、parts 必填；parts 支持 "
                    "box/sphere/cylinder/cone 及 size/position/rotation/color。可传 model_id "
                    "原位替换；不执行任意 JavaScript。"
                ),
                summary="生成声明式3D模型(scene_id+parts)",
                affinity=["design", "3d", "director", "model", "write"],
                cost_profile="mid",
                trusted_source="plugin://director_stage",
                handler=self._model_generate_skill,
            ),
            Skill(
                name="director_stage.model_capture",
                description=(
                    "渲染程序化模型的 front/side/top/iso 多视角 PNG。参数 scene_id、model_id；"
                    "返回本地图片路径，必须实际查看后再做视觉结论。"
                ),
                summary="捕获3D模型多视角(scene_id+model_id)",
                affinity=["design", "3d", "director", "model", "snapshot", "read"],
                cost_profile="mid",
                trusted_source="plugin://director_stage",
                handler=self._model_capture_skill,
            ),
            Skill(
                name="director_stage.model_compare",
                description=(
                    "把模型捕获图与本地参考图做像素差异比较。参数 scene_id、model_id、"
                    "reference_path；返回得分和差异图，但不代替语义与美术判断。"
                ),
                summary="比较3D模型与参考图(scene_id+model_id+reference_path)",
                affinity=["design", "3d", "director", "model", "compare", "read"],
                cost_profile="mid",
                trusted_source="plugin://director_stage",
                handler=self._model_compare_skill,
            ),
            Skill(
                name="director_stage.scene_snapshot",
                description=(
                    "读取导演台当前真实 WebGL 预览图。参数 scene_id 必填，view 可选 director/"
                    "camera。编辑器未打开或尚未渲染时返回 PREVIEW_NOT_READY；返回路径后必须"
                    "实际查看图片。"
                ),
                summary="读取导演台真实预览(scene_id 必填)",
                affinity=["design", "3d", "director", "scene", "snapshot", "read"],
                cost_profile="low",
                trusted_source="plugin://director_stage",
                handler=self._scene_snapshot_skill,
            ),
        ]
        for skill in skills:
            with contextlib.suppress(Exception):
                self.ctx.register_skill(skill)

    def _scene_get_skill(
        self, scene_id: str = "", view: str = "summary", **_kwargs: Any
    ) -> dict[str, Any]:
        try:
            if view not in {"summary", "entities", "timeline", "full"}:
                return {"ok": False, "error": "unsupported view"}
            return {"ok": True, **scene_view(load_scene(_scenes_dir(), scene_id), view)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def _scene_edit_skill(
        self,
        scene_id: str = "",
        operations: list[dict[str, Any]] | None = None,
        validate_only: bool = False,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        try:
            if not operations:
                return {"ok": False, "error": "operations is required"}
            current = load_scene(_scenes_dir(), scene_id)
            updated, result = edit_scene(current, operations, validate_only=validate_only)
            if result["ok"] and not validate_only:
                save_scene(_scenes_dir(), updated)
            return result
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _scene_diagnostics_skill(self, scene_id: str = "", **_kwargs: Any) -> dict[str, Any]:
        try:
            return diagnose_scene(load_scene(_scenes_dir(), scene_id))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def _scene_history_skill(
        self, scene_id: str = "", action: str = "undo", steps: int = 1, **_kwargs: Any
    ) -> dict[str, Any]:
        try:
            scene = load_scene(_scenes_dir(), scene_id)
            steps_taken = 0
            for _ in range(max(1, min(20, int(steps)))):
                scene, moved = step_history(scene, action)
                if not moved:
                    break
                steps_taken += 1
            if steps_taken:
                save_scene(_scenes_dir(), scene)
            history = scene.get("history", {})
            return {
                "ok": steps_taken > 0,
                "action": action,
                "stepsTaken": steps_taken,
                "canUndo": bool(history.get("undo")),
                "canRedo": bool(history.get("redo")),
                "summary": scene_view(scene),
            }
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _motion_read_skill(
        self, scene_id: str = "", motion_id: str = "", **_kwargs: Any
    ) -> dict[str, Any]:
        try:
            return {"ok": True, **read_motion(load_scene(_scenes_dir(), scene_id), motion_id)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def _campath_read_skill(
        self, scene_id: str = "", path_id: str = "", **_kwargs: Any
    ) -> dict[str, Any]:
        try:
            return {
                "ok": True,
                **read_camera_path(load_scene(_scenes_dir(), scene_id), path_id),
            }
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def _model_generate_skill(
        self,
        scene_id: str = "",
        parts: list[dict[str, Any]] | None = None,
        model_id: str | None = None,
        label: str = "程序化模型",
        position: list[float] | None = None,
        rotation: list[float] | None = None,
        scale: list[float] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        try:
            if not parts:
                return {"ok": False, "error": "parts is required"}
            scene = load_scene(_scenes_dir(), scene_id)
            operation: dict[str, Any] = {
                "type": "generate_model",
                "parts": parts,
                "label": label,
                "position": position or [0, 0, 0],
                "rotation": rotation or [0, 0, 0],
                "scale": scale or [1, 1, 1],
            }
            if model_id:
                operation["modelId"] = model_id
            updated, result = edit_scene(scene, [operation])
            if result["ok"]:
                save_scene(_scenes_dir(), updated)
            return result
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _model_capture_skill(
        self,
        scene_id: str = "",
        model_id: str = "",
        views: list[str] | None = None,
        max_dim: int = 640,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        try:
            return capture_model(
                load_scene(_scenes_dir(), scene_id),
                model_id,
                _model_output_dir(scene_id),
                views=views,
                max_dim=max_dim,
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "captures": []}

    def _model_compare_skill(
        self,
        scene_id: str = "",
        model_id: str = "",
        reference_path: str = "",
        view: str = "iso",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        try:
            return compare_model(
                load_scene(_scenes_dir(), scene_id),
                model_id,
                reference_path,
                _model_output_dir(scene_id),
                view=view,
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _scene_snapshot_skill(
        self, scene_id: str = "", view: str = "director", **_kwargs: Any
    ) -> dict[str, Any]:
        try:
            return read_visual_snapshot(_scene_snapshot_dir(scene_id), view=view)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "frames": []}

    def register_routes(self) -> None:
        if self.ctx is None or self.ctx.fastapi_app is None:
            return
        router = APIRouter(prefix="/api/plugins/director-stage", tags=["director_stage"])

        @router.get("/health")
        def health() -> dict[str, Any]:
            return {
                "ok": True,
                "plugin": self.name,
                "local_only": True,
                "methods": [
                    "scene.get",
                    "scene.edit",
                    "scene.history",
                    "scene.snapshot",
                    "scene.diagnostics",
                    "model.generate",
                    "model.capture",
                    "model.compare",
                    "motion.read",
                    "campath.read",
                ],
            }

        @router.get("/scene-schema")
        def scene_schema() -> dict[str, Any]:
            return _SCENE_SCHEMA

        @router.get("/scenes/{scene_id}")
        def get_scene(
            scene_id: str,
            view: str = Query(default="full", pattern="^(summary|entities|timeline|full)$"),
        ) -> dict[str, Any]:
            try:
                return scene_view(load_scene(_scenes_dir(), scene_id), view)
            except ValueError as exc:
                raise HTTPException(404, "scene not found") from exc

        @router.post("/scenes/{scene_id}/edit")
        def update_scene(scene_id: str, body: SceneEditBody) -> dict[str, Any]:
            try:
                current = load_scene(_scenes_dir(), scene_id)
                updated, result = edit_scene(
                    current, body.operations, validate_only=body.validate_only
                )
                if result["ok"] and not body.validate_only:
                    save_scene(_scenes_dir(), updated)
                return result
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

        @router.get("/scenes/{scene_id}/snapshot")
        def snapshot(
            scene_id: str, view: str = Query(default="director", pattern="^(director|camera)$")
        ) -> dict[str, Any]:
            result = self._scene_snapshot_skill(scene_id=scene_id, view=view)
            if not result.get("ok"):
                raise HTTPException(409, str(result.get("error") or "PREVIEW_NOT_READY"))
            return result

        @router.put("/scenes/{scene_id}/visual-snapshot")
        def update_visual_snapshot(scene_id: str, body: VisualSnapshotBody) -> dict[str, Any]:
            try:
                return save_visual_snapshot(
                    _scene_snapshot_dir(scene_id), body.data_url, view=body.view
                )
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

        @router.post("/scenes/{scene_id}/history")
        def history(scene_id: str, body: SceneHistoryBody) -> dict[str, Any]:
            result = self._scene_history_skill(
                scene_id=scene_id, action=body.action, steps=body.steps
            )
            if not result.get("ok") and result.get("error"):
                raise HTTPException(400, str(result["error"]))
            return result

        @router.get("/scenes/{scene_id}/motions/{motion_id}")
        def motion(scene_id: str, motion_id: str) -> dict[str, Any]:
            result = self._motion_read_skill(scene_id=scene_id, motion_id=motion_id)
            if not result.get("ok"):
                raise HTTPException(404, str(result.get("error") or "motion not found"))
            return result

        @router.get("/scenes/{scene_id}/camera-paths/{path_id}")
        def camera_path(scene_id: str, path_id: str) -> dict[str, Any]:
            result = self._campath_read_skill(scene_id=scene_id, path_id=path_id)
            if not result.get("ok"):
                raise HTTPException(404, str(result.get("error") or "path not found"))
            return result

        @router.post("/scenes/{scene_id}/models/generate")
        def generate_model(scene_id: str, body: ModelGenerateBody) -> dict[str, Any]:
            result = self._model_generate_skill(
                scene_id=scene_id,
                model_id=body.model_id,
                label=body.label,
                position=body.position,
                rotation=body.rotation,
                scale=body.scale,
                parts=body.parts,
            )
            if not result.get("ok"):
                raise HTTPException(400, str(result.get("error") or "model generation failed"))
            return result

        @router.post("/scenes/{scene_id}/models/{model_id}/capture")
        def capture(scene_id: str, model_id: str, body: ModelCaptureBody) -> dict[str, Any]:
            result = self._model_capture_skill(
                scene_id=scene_id,
                model_id=model_id,
                views=body.views,
                max_dim=body.max_dim,
            )
            if not result.get("ok"):
                raise HTTPException(400, str(result.get("error") or "model capture failed"))
            return result

        @router.post("/scenes/{scene_id}/models/{model_id}/compare")
        def compare(scene_id: str, model_id: str, body: ModelCompareBody) -> dict[str, Any]:
            result = self._model_compare_skill(
                scene_id=scene_id,
                model_id=model_id,
                reference_path=body.reference_path,
                view=body.view,
            )
            if not result.get("ok"):
                raise HTTPException(400, str(result.get("error") or "model comparison failed"))
            return result

        @router.get("/scenes/{scene_id}/diagnostics")
        def diagnostics(scene_id: str) -> dict[str, Any]:
            try:
                return diagnose_scene(load_scene(_scenes_dir(), scene_id))
            except ValueError as exc:
                raise HTTPException(404, "scene not found") from exc

        self.ctx.fastapi_app.include_router(router)


__all__ = ["DirectorStagePlugin"]
