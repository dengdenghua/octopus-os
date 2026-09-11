"""Agent access to the same authorized photo service used by the desktop.

These tools never accept an actor, a host path, a database path, or credentials
from model arguments. The service and live data policy belong to one app.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from appliance.agent_api.tasks import current_execution_request
from appliance.agent_authorization import require_appliance_actor
from appliance.data_access import DataAccessPolicy, DataAccessScope, DataAccessUnavailable
from appliance.photos import PhotoLibraryService, PhotoPathError


def _failure(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {"ok": False, "code": code, "error": message, "retryable": retryable}


class PhotoToolService:
    def __init__(
        self, service: PhotoLibraryService, data_access: DataAccessPolicy, *, account_security: Any
    ) -> None:
        if account_security is None:
            raise ValueError("photo tools require an app-owned authorization authority")
        self._service = service
        self._data_access = data_access
        self._account_security = account_security

    def _scope(self) -> DataAccessScope:
        actor = require_appliance_actor(expected_security=self._account_security)
        scope = self._data_access.scope_for_actor(actor)
        if scope.actor != actor:
            raise PermissionError("photo scope actor does not match request")
        return scope

    def _invoke(
        self, capability: str, operation: Callable[[DataAccessScope], dict[str, Any]]
    ) -> dict[str, Any]:
        try:
            scope = self._scope()
            request = current_execution_request()
            if request is not None and request.task.actor_id != scope.actor:
                return _failure(
                    "photo_execution_identity_mismatch",
                    "照片任务身份已变化，请重新发起查询。",
                    retryable=True,
                )
            result = operation(scope)
            # A model/index call can outlive revocation or a permission update.
            # Do not publish results captured under a no-longer-valid grant.
            if self._scope() != scope:
                return _failure(
                    "photo_access_changed", "照片访问权限已变化，请重新查询。", retryable=True
                )
            resource_source = result.get("source")
            response = {
                **result,
                "ok": True,
                "source": "appliance.photos",
                "capabilityId": capability,
            }
            if isinstance(resource_source, dict):
                # Keep the existing provider ``source`` string stable for
                # callers while exposing the path-free library identity under
                # an explicit resource key.
                response["resourceSource"] = resource_source
            # The host request is immutable and server-created. Returning its
            # coordinates lets an Agent answer cite which task produced the
            # photo projection without trusting model arguments or exposing a
            # host path. Legacy sessions simply omit this optional envelope.
            if request is not None:
                task = request.task
                response["execution"] = {
                    "taskId": task.task_id,
                    "intentId": task.task_id,
                    "threadId": task.thread_id,
                    **({"parentTaskId": task.parent_task_id} if task.parent_task_id else {}),
                }
            return response
        except PermissionError:
            return _failure(
                "photo_access_denied", "当前设备会话无权读取这些照片，请重新登录或检查共享权限。"
            )
        except DataAccessUnavailable:
            return _failure(
                "photo_access_unavailable", "暂时无法核实照片共享权限，请稍后重试。", retryable=True
            )
        except PhotoPathError:
            return _failure("photo_path_invalid", "照片路径不可用。")
        except (OSError, ValueError):
            return _failure(
                "photo_service_unavailable", "设备照片服务暂不可用，请稍后重试。", retryable=True
            )

    def library(self, *, offset: int = 0, limit: int = 120, search: str = "") -> dict[str, Any]:
        if (
            type(offset) is not int
            or not 0 <= offset <= 100_000
            or type(limit) is not int
            or not 1 <= limit <= 500
            or not isinstance(search, str)
            or len(search) > 120
        ):
            return _failure("invalid_argument", "照片分页或搜索参数无效。")
        return self._invoke(
            "photos.library.list",
            lambda scope: self._service.library(
                offset=offset,
                limit=limit,
                search=search,
                path_visible=None if scope.operator else scope.visible,
            ),
        )

    def search(self, *, query: str, limit: int = 24) -> dict[str, Any]:
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 120
            or type(limit) is not int
            or not 1 <= limit <= 50
        ):
            return _failure(
                "invalid_argument", "请输入不超过 120 字的搜索内容，结果数量应为 1–50。"
            )
        response = self._invoke(
            "photos.search",
            lambda scope: self._service.search(
                query,
                limit=limit,
                path_visible=None if scope.operator else scope.visible,
            ),
        )
        if response.get("ok"):
            response["desktopAction"] = {
                "type": "photos.search", "query": query.strip(),
                "label": "在相册中查看",
                "href": "/#/desktop?" + urlencode({
                    "desktopAction": "photos.search", "query": query.strip(),
                }),
            }
        return response

    def status(self) -> dict[str, Any]:
        return self._invoke(
            "photos.status",
            lambda scope: self._service.status(
                path_visible=None if scope.operator else scope.visible
            ),
        )

    def index_plan(self, *, include_faces: bool = False) -> dict[str, Any]:
        if type(include_faces) is not bool:
            return _failure("invalid_argument", "include_faces 必须为布尔值。")

        def plan(scope: DataAccessScope) -> dict[str, Any]:
            scope.require_operator()
            return self._service.plan_index(include_faces=include_faces)

        return self._invoke("photos.index.plan", plan)


def register_photo_tools(
    registry: Any,
    service: PhotoLibraryService,
    data_access: DataAccessPolicy,
    *,
    account_security: Any,
) -> PhotoToolService:
    from appliance.agent_api.skills import Skill

    bridge = PhotoToolService(service, data_access, account_security=account_security)
    definitions = (
        (
            "photos_library",
            bridge.library,
            "浏览设备相册，与桌面“照片”使用同一图库、索引和成员共享权限。"
            "用户说NAS照片、相册或手机备份照片时使用；不需要文件系统目录。"
            "Args: {offset?: int, limit?: int (1-500), search?: string (文件名)}。"
            "返回当前用户可见的图片路径、元数据和分页总数。",
        ),
        (
            "photos_search",
            bridge.search,
            "搜索设备相册，与桌面“照片”读取同一本地索引，只返回当前用户可见图片。"
            "用户用画面描述查找NAS或手机备份照片时优先使用。"
            "Args: {query: string (1-120字), limit?: int (1-50)}。"
            "结果mode明确semantic或filename，后者仅代表文件名匹配，不能声称已做语义识别。",
        ),
        (
            "photos_status",
            bridge.status,
            "读取设备相册当前可用性和索引状态，与桌面“照片”一致。Args: {}。"
            "成员只看到自己可见图片的统计；仅管理员可看到整库索引任务。",
        ),
        (
            "photos_index_plan",
            bridge.index_plan,
            "为设备管理员预览桌面相册的本地索引计划。Args: {include_faces?: boolean}。"
            "仅预览，不启动索引；返回ready、blockers和requiresApproval。"
            "实际建立仍须在设备照片面板确认并通过单次密码审批，不能把计划就绪报告为完成。",
        ),
    )
    for name, handler, description in definitions:
        registry.register(
            Skill(
                name=name,
                description=description,
                handler=handler,
                affinity=["image", "photos", "nas", "search"],
                cost_profile="low",
                trusted_source=f"builtin://appliance/{name}",
                replay_policy="refresh_read",
                privacy_local=True,
            )
        )
    return bridge


__all__ = ["PhotoToolService", "register_photo_tools"]
