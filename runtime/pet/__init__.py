"""桌面宠物相关：事件语义映射（Agent 状态 / 情绪 / 疲劳 / 跨设备在场）。"""

from .pet_state_map import (
    VALID_EMOTIONS,
    PetEvent,
    emotion_event,
    map_agent_state,
    map_tentacle_event,
    presence_event,
    tired_event,
)
from .udp_bridge import PetUdpBridge

__all__ = [
    "VALID_EMOTIONS",
    "PetEvent",
    "PetUdpBridge",
    "emotion_event",
    "map_agent_state",
    "map_tentacle_event",
    "presence_event",
    "tired_event",
]
