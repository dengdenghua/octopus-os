"""Echo local clip-studio surface."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from runtime.execution.suckers.registry import Skill
from runtime.platform.plugins.plugin_base import ModulePlugin
from runtime.platform.process.paths import app_paths

from .project_store import (
    diagnose_project,
    edit_project,
    load_project,
    project_view,
    save_project,
    step_history,
)
from .snapshot_renderer import render_project_frames, sample_times
from .video_export import encode_project_video


class ProjectEditBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    description: str = Field(default="编辑时间线", max_length=160)
    operations: list[dict[str, Any]] = Field(min_length=1, max_length=50)
    validate_only: bool = Field(
        default=False,
        validation_alias=AliasChoices("validate_only", "validateOnly"),
    )


class ProjectHistoryBody(BaseModel):
    action: str
    steps: int = Field(default=1, ge=1, le=20)


class ProjectViewBody(BaseModel):
    action: str
    to_sec: float | None = Field(default=None, validation_alias=AliasChoices("to_sec", "toSec"))
    from_sec: float | None = Field(
        default=None, validation_alias=AliasChoices("from_sec", "fromSec")
    )


class ProjectSnapshotBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    times: list[float] | None = Field(default=None, max_length=8)
    from_sec: float | None = Field(
        default=None, validation_alias=AliasChoices("from_sec", "fromSec")
    )
    to_sec: float | None = Field(default=None, validation_alias=AliasChoices("to_sec", "toSec"))
    count: int = Field(default=4, ge=1, le=8)
    max_dim: int = Field(
        default=640,
        ge=160,
        le=1280,
        validation_alias=AliasChoices("max_dim", "maxDim"),
    )


class ProjectExportBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_dim: int = Field(
        default=1280,
        ge=160,
        le=1280,
        validation_alias=AliasChoices("max_dim", "maxDim"),
    )
    include_audio: bool = Field(
        default=True,
        validation_alias=AliasChoices("include_audio", "includeAudio"),
    )


def _projects_dir() -> Path:
    target = app_paths().data_dir / "design" / "clip-studio-projects"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _snapshots_dir(project_id: str) -> Path:
    target = app_paths().data_dir / "design" / "clip-studio-snapshots" / project_id
    target.mkdir(parents=True, exist_ok=True)
    return target


def _exports_dir(project_id: str) -> Path:
    target = app_paths().data_dir / "design" / "clip-studio-exports" / project_id
    target.mkdir(parents=True, exist_ok=True)
    return target


class ClipStudioPlugin(ModulePlugin):
    name = "clip_studio"
    display_name = "AI 剪辑工坊"
    version = "0.7.0"
    description = "本地多轨视频剪辑、字幕与项目编排工作台"
    author = "Echo"

    def register_skills(self) -> None:
        if self.ctx is None:
            return
        skills = [
            Skill(
                name="clip_studio.project_get",
                description=(
                    "读取本地 AI 剪辑工坊项目。参数 project_id 必填；view 可选 "
                    "summary/tracks/clips/full，默认 clips。返回轨道、片段、字幕与时长。"
                ),
                summary="读取剪辑项目(project_id 必填)",
                affinity=["design", "video", "editing", "timeline", "read"],
                cost_profile="low",
                trusted_source="plugin://clip_studio",
                handler=self._project_get_skill,
            ),
            Skill(
                name="clip_studio.project_edit",
                description=(
                    "原子批量编辑本地剪辑项目。参数 project_id 与 operations 必填；所有操作"
                    "全部成功才落盘，任一步失败会整体回滚。支持导入媒体、添加字幕、移动/裁剪/"
                    "分割/波纹删除片段、真实音频静音剪切、SRT 字幕、转场、效果、调色、"
                    "轨道与标记操作。"
                    "validate_only=true 时仅验证不写入。"
                ),
                summary="原子编辑剪辑时间线(project_id+operations)",
                affinity=["design", "video", "editing", "timeline", "write"],
                cost_profile="mid",
                trusted_source="plugin://clip_studio",
                handler=self._project_edit_skill,
            ),
            Skill(
                name="clip_studio.project_diagnostics",
                description=(
                    "检查本地剪辑项目的空轨道、片段重叠、黑场间隙、丢失媒体和字幕越界。"
                    "参数 project_id 必填；不修改项目。"
                ),
                summary="诊断剪辑时间线(project_id 必填)",
                affinity=["design", "video", "editing", "diagnostics", "read"],
                cost_profile="low",
                trusted_source="plugin://clip_studio",
                handler=self._project_diagnostics_skill,
            ),
            Skill(
                name="clip_studio.project_snapshot",
                description=(
                    "从剪辑时间线生成真实合成帧。参数 project_id 必填；可传 times(1-8) 或 "
                    "from_sec/to_sec/count，max_dim 最大 1280。返回本地 PNG 路径；调用后必须实际"
                    "查看图片再做视觉结论。"
                ),
                summary="渲染剪辑时间线帧(project_id+times/range)",
                affinity=["design", "video", "editing", "snapshot", "read"],
                cost_profile="mid",
                trusted_source="plugin://clip_studio",
                handler=self._project_snapshot_skill,
            ),
            Skill(
                name="clip_studio.project_history",
                description=(
                    "撤销或重做剪辑项目的一到二十个历史步骤。参数 project_id、action "
                    "(undo/redo) 必填，steps 可选。"
                ),
                summary="撤销或重做剪辑项目(project_id+action)",
                affinity=["design", "video", "editing", "timeline", "history", "write"],
                cost_profile="low",
                trusted_source="plugin://clip_studio",
                handler=self._project_history_skill,
            ),
            Skill(
                name="clip_studio.project_view",
                description=(
                    "控制剪辑工坊播放头。参数 project_id、action(seek/play/pause) 必填；"
                    "seek 需要 to_sec，play 可传 from_sec。"
                ),
                summary="定位或播放剪辑项目(project_id+action)",
                affinity=["design", "video", "editing", "timeline", "view", "write"],
                cost_profile="low",
                trusted_source="plugin://clip_studio",
                handler=self._project_view_skill,
            ),
            Skill(
                name="clip_studio.project_export",
                description=(
                    "把本地剪辑时间线编码成真实 MP4。参数 project_id 必填；max_dim 最大 "
                    "1280，include_audio 默认 true。导出会合成可见多层、关键帧、效果、转场、"
                    "字幕和可用音轨，最长 120 秒，返回本地文件路径。"
                ),
                summary="导出剪辑成片(project_id 必填)",
                affinity=["design", "video", "editing", "export", "render", "write"],
                cost_profile="high",
                trusted_source="plugin://clip_studio",
                handler=self._project_export_skill,
            ),
        ]
        for skill in skills:
            with contextlib.suppress(Exception):
                self.ctx.register_skill(skill)

    def _project_get_skill(
        self, project_id: str = "", view: str = "clips", **_kwargs: Any
    ) -> dict[str, Any]:
        try:
            if view not in {"summary", "tracks", "clips", "full"}:
                return {"ok": False, "error": "unsupported view"}
            return {"ok": True, **project_view(load_project(_projects_dir(), project_id), view)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def _project_edit_skill(
        self,
        project_id: str = "",
        operations: list[dict[str, Any]] | None = None,
        validate_only: bool = False,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        try:
            if not operations:
                return {"ok": False, "error": "operations is required"}
            current = load_project(_projects_dir(), project_id)
            updated, result = edit_project(
                current,
                operations,
                validate_only=validate_only,
            )
            if result["ok"] and not validate_only:
                save_project(_projects_dir(), updated)
            return result
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _project_diagnostics_skill(self, project_id: str = "", **_kwargs: Any) -> dict[str, Any]:
        try:
            return diagnose_project(load_project(_projects_dir(), project_id))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def _project_snapshot_skill(
        self,
        project_id: str = "",
        times: list[float] | None = None,
        from_sec: float | None = None,
        to_sec: float | None = None,
        count: int = 4,
        max_dim: int = 640,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        try:
            project = load_project(_projects_dir(), project_id)
            requested = sample_times(
                project,
                times=times,
                from_sec=from_sec,
                to_sec=to_sec,
                count=count,
            )
            return render_project_frames(
                project,
                _snapshots_dir(project_id),
                times=requested,
                max_dim=max_dim,
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "frames": []}

    def _project_history_skill(
        self,
        project_id: str = "",
        action: str = "",
        steps: int = 1,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if action not in {"undo", "redo"}:
            return {"ok": False, "error": "action must be undo or redo"}
        try:
            project = load_project(_projects_dir(), project_id)
            steps_taken = 0
            for _ in range(max(1, min(20, int(steps)))):
                project, moved = step_history(project, action)
                if not moved:
                    break
                steps_taken += 1
            if steps_taken:
                save_project(_projects_dir(), project)
            history_state = project.get("history", {})
            return {
                "ok": steps_taken > 0,
                "action": action,
                "stepsTaken": steps_taken,
                "canUndo": bool(history_state.get("undo")),
                "canRedo": bool(history_state.get("redo")),
                "summary": project_view(project, "summary"),
            }
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _project_view_skill(
        self,
        project_id: str = "",
        action: str = "",
        to_sec: float | None = None,
        from_sec: float | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if action not in {"seek", "play", "pause"}:
            return {"ok": False, "error": "action must be seek, play, or pause"}
        if action == "seek" and to_sec is None:
            return {"ok": False, "error": "seek requires to_sec"}
        try:
            project = load_project(_projects_dir(), project_id)
            if action == "seek":
                project["playheadSec"] = max(0, float(to_sec or 0))
            elif action == "play" and from_sec is not None:
                project["playheadSec"] = max(0, float(from_sec))
            project["playbackState"] = "playing" if action == "play" else "paused"
            save_project(_projects_dir(), project)
            return {
                "ok": True,
                "action": action,
                "playheadSec": project.get("playheadSec", 0),
                "playbackState": project["playbackState"],
            }
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _project_export_skill(
        self,
        project_id: str = "",
        max_dim: int = 1280,
        include_audio: bool = True,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        try:
            return encode_project_video(
                load_project(_projects_dir(), project_id),
                _exports_dir(project_id),
                max_dim=max_dim,
                include_audio=include_audio,
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def register_routes(self) -> None:
        if self.ctx is None or self.ctx.fastapi_app is None:
            return
        page_path = Path(self.ctx.plugin_dir) / "page" / "index.html"
        router = APIRouter(prefix="/api/plugins/clip-studio", tags=["clip_studio"])

        @router.get("/page", response_class=HTMLResponse)
        def page() -> HTMLResponse:
            content = "AI 剪辑工坊页面缺失"
            if page_path.is_file():
                content = page_path.read_text(encoding="utf-8")
            return HTMLResponse(content=content)

        @router.get("/health")
        def health() -> dict[str, Any]:
            return {
                "ok": True,
                "plugin": self.name,
                "local_only": True,
                "methods": [
                    "project.get",
                    "project.edit",
                    "project.history",
                    "project.view",
                    "project.snapshot",
                    "project.diagnostics",
                    "project.export",
                ],
            }

        @router.get("/projects/{project_id}")
        def get_project(
            project_id: str,
            view: str = Query(default="clips", pattern="^(summary|tracks|clips|full)$"),
        ) -> dict[str, Any]:
            try:
                return project_view(load_project(_projects_dir(), project_id), view)
            except ValueError as exc:
                raise HTTPException(404, "project not found") from exc

        @router.post("/projects/{project_id}/edit")
        def edit(project_id: str, body: ProjectEditBody) -> dict[str, Any]:
            try:
                current = load_project(_projects_dir(), project_id)
                updated, result = edit_project(
                    current,
                    body.operations,
                    validate_only=body.validate_only,
                )
                if result["ok"] and not body.validate_only:
                    save_project(_projects_dir(), updated)
                return result
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

        @router.post("/projects/{project_id}/history")
        def history(project_id: str, body: ProjectHistoryBody) -> dict[str, Any]:
            if body.action not in {"undo", "redo"}:
                raise HTTPException(400, "action must be undo or redo")
            try:
                project = load_project(_projects_dir(), project_id)
                steps_taken = 0
                for _ in range(body.steps):
                    project, moved = step_history(project, body.action)
                    if not moved:
                        break
                    steps_taken += 1
                if steps_taken:
                    save_project(_projects_dir(), project)
                history_state = project.get("history", {})
                return {
                    "ok": steps_taken > 0,
                    "action": body.action,
                    "stepsTaken": steps_taken,
                    "canUndo": bool(history_state.get("undo")),
                    "canRedo": bool(history_state.get("redo")),
                    "summary": project_view(project, "summary"),
                }
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

        @router.post("/projects/{project_id}/view")
        def view_project(project_id: str, body: ProjectViewBody) -> dict[str, Any]:
            if body.action not in {"seek", "play", "pause"}:
                raise HTTPException(400, "unsupported view action")
            try:
                project = load_project(_projects_dir(), project_id)
                if body.action == "seek" and body.to_sec is None:
                    raise HTTPException(400, "seek requires toSec")
                if body.action == "seek":
                    project["playheadSec"] = max(0, float(body.to_sec or 0))
                elif body.action == "play" and body.from_sec is not None:
                    project["playheadSec"] = max(0, float(body.from_sec))
                project["playbackState"] = "playing" if body.action == "play" else "paused"
                save_project(_projects_dir(), project)
                return {
                    "ok": True,
                    "action": body.action,
                    "playheadSec": project.get("playheadSec", 0),
                }
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

        @router.post("/projects/{project_id}/snapshot")
        def snapshot(project_id: str, body: ProjectSnapshotBody) -> dict[str, Any]:
            result = self._project_snapshot_skill(
                project_id=project_id,
                times=body.times,
                from_sec=body.from_sec,
                to_sec=body.to_sec,
                count=body.count,
                max_dim=body.max_dim,
            )
            if not result.get("ok"):
                raise HTTPException(400, str(result.get("error") or "snapshot failed"))
            return result

        @router.get("/projects/{project_id}/diagnostics")
        def diagnostics(project_id: str) -> dict[str, Any]:
            try:
                return diagnose_project(load_project(_projects_dir(), project_id))
            except ValueError as exc:
                raise HTTPException(404, "project not found") from exc

        @router.post("/projects/{project_id}/export")
        def export_project(project_id: str, body: ProjectExportBody) -> dict[str, Any]:
            result = self._project_export_skill(
                project_id=project_id,
                max_dim=body.max_dim,
                include_audio=body.include_audio,
            )
            if not result.get("ok"):
                raise HTTPException(400, str(result.get("error") or "export failed"))
            return result

        @router.get("/projects/{project_id}/export/file")
        def export_file(project_id: str) -> FileResponse:
            load_project(_projects_dir(), project_id)  # validates the scoped project id
            path = _exports_dir(project_id) / "export.mp4"
            if not path.is_file():
                raise HTTPException(404, "export not found")
            return FileResponse(path, media_type="video/mp4", filename=f"{project_id}.mp4")

        self.ctx.fastapi_app.include_router(router)


__all__ = ["ClipStudioPlugin"]
