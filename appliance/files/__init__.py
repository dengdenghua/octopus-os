"""NAS 文件管理器:浏览/移动/删除(回收站语义)宿主存储。

删除一律移入回收站(.octopus-trash),物理删除仅 empty_trash 一条路径——
兑现 docs/OCTOPUS_OS_PLAN.md 的硬约束。
"""

from appliance.files.manager import FileManager, PathEscape
from appliance.files.router import create_files_router

__all__ = ["FileManager", "PathEscape", "create_files_router"]
