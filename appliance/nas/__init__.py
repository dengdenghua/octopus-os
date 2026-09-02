"""NAS 管控面 —— Echo OS 的"NAS 那一半"。

echo-os 原本只有桌面壳(启动器 + 文件管理器 + agent 对话),缺的是让一台
设备真正成为 NAS 的部分:存储池、共享协议、磁盘健康。本包补上这一层。

分层坚持两条铁律(见 docs/P3_provision_BASE_PLAN.md):

- **不自研存储。** ZFS 用 OpenZFS 官方包,阵列用 mdadm/lvm,共享用 Samba/NFS
  官方包,统一命名空间用 dataset + bind mount。参考 NAS自研 ``nasfs`` 内核模块
  的代价是每跟一个内核版本重适配一次,收益却能被上述组合覆盖九成。
- **配置片段 + reload,而非注册中心。** 与参考 NAS nginx ``conf.d/`` 同一套模式:
  共享清单(shares.json)是唯一真源,配置文件可由清单全量重建。
"""

from appliance.nas.router import ShareIn, create_nas_router
from appliance.nas.shares import Share, ShareError, ShareManager
from appliance.nas.storage import StorageInspector

__all__ = [
    "Share",
    "ShareError",
    "ShareIn",
    "ShareManager",
    "StorageInspector",
    "create_nas_router",
]
