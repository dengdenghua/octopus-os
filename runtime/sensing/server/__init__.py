"""Sandbox backends · Docker / K8s / SSH / subprocess · **未自动接线**。

⚠️ **实装状态（2026-06-30 核查）**：本包的全部 backend 类
（``DockerBackend`` / ``K8sBackend`` / ``SshBackend`` / ``SubprocessBackend``）
均有完整实现和配套测试，但**没有任何生产代码 import 它们**——
运行时的沙箱执行实际走 ``runtime/safety/sandboxing/``（独立的
``Backend`` Protocol + ``DirectBackend`` + ``ContainerSandbox``）。

本包是预留的公共 API，供 operator 手动实例化远程/容器化执行
backend 时使用。若团队确认不再需要，可整包删除（含 ``tests/``
下的 ``test_docker_backend.py`` / ``test_k8s_backend.py`` /
``test_ssh_backend.py`` / ``test_subprocess_backend.py``）。
"""

from .docker import DockerBackend, DockerSandbox, DockerUnavailableError
from .k8s import K8sBackend, K8sSandbox, K8sUnavailableError
from .local import BackendAudit, LocalBackend, Sandbox
from .ssh import SshBackend, SshSandbox, SshUnavailableError
from .subprocess_backend import SubprocessBackend, SubprocessSandbox

__all__ = [
    "DockerSandbox",
    "DockerBackend",
    "DockerUnavailableError",
    "K8sSandbox",
    "K8sBackend",
    "K8sUnavailableError",
    "LocalBackend",
    "BackendAudit",
    "Sandbox",
    "SshSandbox",
    "SshBackend",
    "SshUnavailableError",
    "SubprocessSandbox",
    "SubprocessBackend",
]
