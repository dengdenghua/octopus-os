"""桌面宠物事件映射：Agent 状态 / 情绪 / 疲劳 / 跨设备在场 → 宠物事件。

echo 的桌面宠物（Godot sidecar）经 UDP 8765 监听 ``agent.*`` 事件
（``frontend/src/lib/pet-ipc.ts`` 与 ``frontend/electron/pet-sidecar.cjs``
负责发送，`world.windows` 用于避障）。本模块是事件语义的**权威映射源**
（纯函数、可单测、无 I/O）：

* ``map_agent_state`` —— Agent 运行时状态（idle/thinking/working/...）→
  ``agent.*`` 事件，与 Electron 侧 ``petEventForAgentState`` 语义一致，
  并把 ``streaming`` 归入思考（避免宠物在首字流出时乱跳）；
* ``emotion_event`` —— 情绪语义（happy/sad/curious/surprised/concerned），
  白名单 + 强度钳制；
* ``tired_event`` —— 疲劳语义（长时间高负载后），强度 0-1；
* ``presence_event`` —— 在场语义：主人 / 设备上线离线（``agent.presence``）；
* ``map_tentacle_event`` —— 跨设备在场的桥：``TentaclePool`` 的
  ``tentacle.registered / tentacle.unregistered`` 事件 → 宠物在场事件，
  让"手机（触手）连上 / 断开"在桌面上有可感知的在场表现。

事件一律 best-effort：非法输入返回 ``None``（调用方视为 no-op），
绝不抛出、绝不改变上游行为。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 情绪白名单：与 Godot PetBrain 的情绪分支一一对应。
VALID_EMOTIONS = frozenset({"happy", "sad", "curious", "surprised", "concerned"})

# Agent 运行时状态 → 宠物事件类型的映射（``streaming`` 归入思考）。
_AGENT_STATE_EVENTS = {
    "idle": "agent.idle",
    "thinking": "agent.thinking",
    "working": "agent.working",
    "waiting_user": "agent.waiting_user",
    "success": "agent.success",
    "error": "agent.error",
    "streaming": "agent.thinking",
}


@dataclass(frozen=True)
class PetEvent:
    """一条宠物事件：事件类型 + 可选负载（JSON-safe，可直接 UDP 发送）。"""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.payload}


def map_agent_state(state: str) -> PetEvent | None:
    """Agent 运行时状态 → 宠物事件；未知状态返回 None（no-op）。"""
    key = str(state or "").strip().lower()
    event_type = _AGENT_STATE_EVENTS.get(key)
    if not event_type:
        return None
    if key == "working":
        return PetEvent(event_type, {"intensity": 0.6})
    return PetEvent(event_type)


def emotion_event(emotion: str, *, intensity: float = 1.0) -> PetEvent | None:
    """情绪事件；非白名单情绪返回 None。强度钳制在 [0, 1]。"""
    key = str(emotion or "").strip().lower()
    if key not in VALID_EMOTIONS:
        return None
    return PetEvent(
        "agent.emotion",
        {"emotion": key, "intensity": _clamp01(intensity, 1.0)},
    )


def tired_event(intensity: float = 0.5) -> PetEvent:
    """疲劳事件；强度钳制在 [0, 1]（默认 0.5 = 中度疲劳）。"""
    return PetEvent("agent.tired", {"intensity": _clamp01(intensity, 0.5)})


def presence_event(*, online: bool, device_id: str = "") -> PetEvent:
    """在场事件：主人 / 设备上线或离线。"""
    return PetEvent(
        "agent.presence",
        {"online": bool(online), "device_id": str(device_id or "")},
    )


def map_tentacle_event(event_type: str, data: dict[str, Any] | None) -> PetEvent | None:
    """跨设备在场的桥：TentaclePool 的注册事件 → 宠物在场事件。

    ``tentacle.registered`` → 设备在线；``tentacle.unregistered`` → 设备
    离线。其它事件返回 None（no-op）。设备 id 取 ``tentacle_id``。
    """
    key = str(event_type or "").strip()
    payload = data if isinstance(data, dict) else {}
    device_id = str(payload.get("tentacle_id") or "")
    if key == "tentacle.registered":
        return presence_event(online=True, device_id=device_id)
    if key == "tentacle.unregistered":
        return presence_event(online=False, device_id=device_id)
    return None


def _clamp01(value: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(0.0, min(1.0, number))


__all__ = [
    "VALID_EMOTIONS",
    "PetEvent",
    "emotion_event",
    "map_agent_state",
    "map_tentacle_event",
    "presence_event",
    "tired_event",
]
