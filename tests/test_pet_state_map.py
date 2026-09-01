"""Tests for ``runtime.pet.pet_state_map`` (P2 · 桌面宠物物理在场强化).

Covers:
  1. ``map_agent_state``：全部运行时状态 → 宠物事件（含 streaming 归入 thinking）
  2. ``emotion_event``：情绪白名单 + 强度钳制 + 非法情绪 no-op
  3. ``tired_event`` / ``presence_event``：强度/在场语义
  4. ``map_tentacle_event``：TentaclePool 注册/注销事件 → 跨设备在场
  5. JSON-safe 输出（to_dict 可直接进 UDP 负载）
"""

from __future__ import annotations

import pytest

from runtime.pet.pet_state_map import (
    VALID_EMOTIONS,
    PetEvent,
    emotion_event,
    map_agent_state,
    map_tentacle_event,
    presence_event,
    tired_event,
)

# ── map_agent_state ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("state", "expected_type", "expected_payload"),
    [
        ("idle", "agent.idle", {}),
        ("thinking", "agent.thinking", {}),
        ("working", "agent.working", {"intensity": 0.6}),
        ("waiting_user", "agent.waiting_user", {}),
        ("success", "agent.success", {}),
        ("error", "agent.error", {}),
        ("streaming", "agent.thinking", {}),  # 首字流出 = 思考，不打断宠物
        ("IDLE", "agent.idle", {}),  # 大小写不敏感
    ],
)
def test_map_agent_state(state: str, expected_type: str, expected_payload: dict) -> None:
    event = map_agent_state(state)
    assert event is not None
    assert event.type == expected_type
    assert event.payload == expected_payload


def test_map_agent_state_unknown_is_noop() -> None:
    assert map_agent_state("") is None
    assert map_agent_state(None) is None  # type: ignore[arg-type]
    assert map_agent_state("unknown_state") is None
    assert map_agent_state("   ") is None


# ── 情绪 / 疲劳 / 在场 ──────────────────────────────────────────────────────


def test_emotion_event_whitelist() -> None:
    assert {"happy", "sad", "curious", "surprised", "concerned"} == VALID_EMOTIONS
    event = emotion_event("happy")
    assert event is not None and event.type == "agent.emotion"
    assert event.payload == {"emotion": "happy", "intensity": 1.0}


@pytest.mark.parametrize("bad", ["", "angry", "MEH", "  ", None])
def test_emotion_event_rejects_unknown(bad: str | None) -> None:
    assert emotion_event(bad) is None  # type: ignore[arg-type]


def test_emotion_intensity_clamped() -> None:
    assert emotion_event("sad", intensity=2.5).payload["intensity"] == 1.0  # type: ignore[union-attr]
    assert emotion_event("sad", intensity=-1.0).payload["intensity"] == 0.0  # type: ignore[union-attr]
    assert emotion_event("curious", intensity="junk").payload["intensity"] == 1.0  # type: ignore[union-attr]


def test_tired_event_intensity_clamped() -> None:
    assert tired_event().payload == {"intensity": 0.5}
    assert tired_event(3.0).payload["intensity"] == 1.0
    assert tired_event(-3.0).payload["intensity"] == 0.0


def test_presence_event() -> None:
    on = presence_event(online=True, device_id="android-1")
    assert on.type == "agent.presence"
    assert on.payload == {"online": True, "device_id": "android-1"}
    off = presence_event(online=False)
    assert off.payload == {"online": False, "device_id": ""}


# ── 跨设备在场桥 ────────────────────────────────────────────────────────────


def test_map_tentacle_event_bridges_pool_to_pet() -> None:
    registered = map_tentacle_event("tentacle.registered", {"tentacle_id": "phone-7"})
    assert registered is not None and registered.type == "agent.presence"
    assert registered.payload == {"online": True, "device_id": "phone-7"}

    unregistered = map_tentacle_event("tentacle.unregistered", {"tentacle_id": "phone-7"})
    assert unregistered is not None and unregistered.type == "agent.presence"
    assert unregistered.payload == {"online": False, "device_id": "phone-7"}


def test_map_tentacle_event_unknown_is_noop() -> None:
    assert map_tentacle_event("tentacle.working", {"tentacle_id": "x"}) is None
    assert map_tentacle_event("", None) is None
    assert map_tentacle_event(None, None) is None  # type: ignore[arg-type]
    # 注册事件即使没有 data 也映射（在线在场，设备 id 为空）
    registered = map_tentacle_event("tentacle.registered", None)
    assert registered is not None and registered.payload == {"online": True, "device_id": ""}


# ── 输出形态 ────────────────────────────────────────────────────────────────


def test_pet_event_to_dict_is_udp_ready() -> None:
    event = emotion_event("surprised", intensity=0.8)
    assert event is not None
    blob = event.to_dict()
    assert blob == {"type": "agent.emotion", "emotion": "surprised", "intensity": 0.8}
    import json

    json.dumps(blob)  # 可直接序列化进 UDP 负载


def test_pet_event_default_payload_is_shared_safe() -> None:
    event = PetEvent("agent.idle")
    assert event.to_dict() == {"type": "agent.idle"}
    assert event.payload == {}  # frozen dataclass 的默认字段独立

