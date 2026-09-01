"""通信传输层.

Phase 1 实现：WebSocket Server（echo-agent 端）接收 Echo Mobile 连接.

导出：
  - TentacleWebSocketServer: WebSocket 服务器
  - DeviceHello: 设备注册消息
  - TaskExecuteRequest: 任务执行请求
"""

from .ws_server import DeviceHello, TaskExecuteRequest, TentacleWebSocketServer

__all__ = [
    "TentacleWebSocketServer",
    "DeviceHello",
    "TaskExecuteRequest",
]
