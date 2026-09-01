from pathlib import Path
from types import SimpleNamespace

from runtime.platform.ui._app_routers import _workspaces_router_root


def test_workspaces_router_uses_execution_workspace_root() -> None:
    root = Path("/tmp/echo-project-workspaces")

    assert _workspaces_router_root(SimpleNamespace(thread_workspace_root=root)) == root

