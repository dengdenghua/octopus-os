"""Persistent, dependency-free scene engine for the Echo director stage."""

from __future__ import annotations

import copy
import json
import math
import re
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

_SAFE_ID = re.compile(r"^[a-zA-Z0-9._-]{1,160}$")
_LOCK = threading.RLock()
_POSES = {
    "stand",
    "tpose",
    "walk",
    "run",
    "jump",
    "sit",
    "squat",
    "kneel",
    "lie",
    "drive",
    "wave",
    "hands_up",
    "bow",
    "akimbo",
    "think",
    "fight",
    "aim",
    "sword",
    "spell",
}

_PROP_CATALOG: dict[str, dict[str, Any]] = {
    "chair": {"shape": "box", "size": [0.65, 0.9, 0.65], "color": "#b98962"},
    "sofa": {"shape": "box", "size": [2.0, 0.85, 0.85], "color": "#7186a6"},
    "square_table": {"shape": "box", "size": [1.2, 0.72, 1.2], "color": "#9b7653"},
    "desk": {"shape": "box", "size": [1.8, 0.75, 0.8], "color": "#8b6b4a"},
    "bed": {"shape": "box", "size": [1.6, 0.5, 2.2], "color": "#d6d3c8"},
    "wall": {"shape": "box", "size": [3.0, 2.6, 0.18], "color": "#d5dae3"},
    "platform": {"shape": "box", "size": [2.4, 0.3, 2.4], "color": "#aeb8c7"},
    "column": {"shape": "cylinder", "size": [0.45, 2.8, 0.45], "color": "#cbd3df"},
    "tree_small": {"shape": "cone", "size": [1.2, 2.4, 1.2], "color": "#5f8c62"},
    "rock": {"shape": "sphere", "size": [1.0, 0.65, 0.8], "color": "#7c8490"},
    "car": {"shape": "box", "size": [1.8, 1.25, 4.1], "color": "#577da8"},
    "bench": {"shape": "box", "size": [1.8, 0.55, 0.55], "color": "#9b7047"},
    "crate": {"shape": "box", "size": [0.8, 0.8, 0.8], "color": "#a77a48"},
    "barrel": {"shape": "cylinder", "size": [0.65, 1.0, 0.65], "color": "#63758b"},
}
PROP_CATALOG = tuple(sorted(_PROP_CATALOG))
_BUILTIN_MOTIONS: dict[str, dict[str, Any]] = {
    "walk": {
        "label": "行走",
        "loop": True,
        "cycleMs": 1000,
        "defaultMs": 3000,
        "source": "0ms pose stand\n250ms step left\n500ms pose stand\n750ms step right\n1000ms pose stand",
    },
    "run": {
        "label": "跑步",
        "loop": True,
        "cycleMs": 640,
        "defaultMs": 2560,
        "source": "0ms lean 10\n160ms step left\n320ms lean 10\n480ms step right\n640ms lean 10",
    },
    "bow": {
        "label": "鞠躬",
        "loop": False,
        "cycleMs": 1400,
        "defaultMs": 1400,
        "source": "0ms pose stand\n500ms torso 35\n900ms torso 35\n1400ms pose stand",
    },
}


def new_scene(scene_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "id": scene_id,
        "scene": {
            "name": "未命名场景",
            "scale": [1, 1, 1],
            "position": [0, 0, 0],
            "rotation": [0, 0, 0],
            "skyColor": "#dce6f2",
            "background": "studio",
            "backgroundMode": "panorama",
            "backgroundImage": None,
            "backgroundImageName": None,
            "horizontalRotation": 0,
            "sphereRadius": 90,
            "showRoleLabels": True,
        },
        "entities": [
            {
                "id": "character-1",
                "type": "character",
                "name": "角色 1",
                "bodyType": "mannequin",
                "pose": "stand",
                "position": [0, 0, 0],
                "rotation": [0, 0, 0],
                "scale": [1, 1, 1],
            },
            {
                "id": "camera-1",
                "type": "camera",
                "name": "相机 1",
                "position": [0, 1.6, 5],
                "target": [0, 1, 0],
                "focalLength": 50,
                "aspectRatio": "16:9",
            },
        ],
        "timeline": {"durationSec": 5, "tracks": []},
        "motions": [],
        "revision": 0,
        "history": {"undo": [], "redo": []},
    }


def load_scene(root: Path, scene_id: str) -> dict[str, Any]:
    path = _path(root, scene_id)
    with _LOCK:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return new_scene(scene_id)
        except (OSError, json.JSONDecodeError):
            return new_scene(scene_id)
    return payload if isinstance(payload, dict) else new_scene(scene_id)


def save_scene(root: Path, scene: dict[str, Any]) -> None:
    path = _path(root, str(scene.get("id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with _LOCK:
        temporary.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def scene_view(scene: dict[str, Any], view: str = "summary") -> dict[str, Any]:
    entities = scene.get("entities", [])
    tracks = scene.get("timeline", {}).get("tracks", [])
    summary = {
        "id": scene.get("id"),
        "scene": scene.get("scene", {}),
        "counts": {
            "entities": len(entities),
            "characters": sum(item.get("type") == "character" for item in entities),
            "cameras": sum(item.get("type") == "camera" for item in entities),
            "models": sum(item.get("type") == "model" for item in entities),
            "tracks": len(tracks),
        },
        "durationSec": scene.get("timeline", {}).get("durationSec", 0),
        "revision": int(scene.get("revision") or 0),
    }
    if view in {"entities", "full"}:
        summary["entities"] = entities
    if view in {"timeline", "full"}:
        summary["timeline"] = scene.get("timeline", {})
        summary["motions"] = scene.get("motions", [])
    return summary


def edit_scene(
    scene: dict[str, Any], operations: list[dict[str, Any]], *, validate_only: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    draft = copy.deepcopy(scene)
    results: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        try:
            detail = _apply(draft, operation)
            results.append({"index": index, "ok": True, **detail})
        except (KeyError, TypeError, ValueError) as exc:
            return scene, {
                "ok": False,
                "applied": 0,
                "failed": 1,
                "rolledBack": True,
                "results": [*results, {"index": index, "ok": False, "detail": str(exc)}],
            }
    if not validate_only:
        prior = _without_history(scene)
        history = draft.setdefault("history", {"undo": [], "redo": []})
        history["undo"] = [*scene.get("history", {}).get("undo", []), prior][-20:]
        history["redo"] = []
        draft["revision"] = int(scene.get("revision") or 0) + 1
    return (scene if validate_only else draft), {
        "ok": True,
        "validateOnly": validate_only,
        "applied": 0 if validate_only else len(operations),
        "failed": 0,
        "rolledBack": False,
        "results": results,
        "summary": scene_view(draft),
        "revision": int(draft.get("revision") or 0),
    }


def step_history(scene: dict[str, Any], action: str) -> tuple[dict[str, Any], bool]:
    if action not in {"undo", "redo"}:
        raise ValueError("action must be undo or redo")
    history = scene.get("history", {})
    source = list(history.get(action, []))
    if not source:
        return scene, False
    restored = copy.deepcopy(source.pop())
    other = "redo" if action == "undo" else "undo"
    restored["history"] = {
        action: source,
        other: [*history.get(other, []), _without_history(scene)][-20:],
    }
    restored["revision"] = int(scene.get("revision") or 0) + 1
    return restored, True


def read_motion(scene: dict[str, Any], motion_id: str) -> dict[str, Any]:
    builtin_matches = [
        (key, value) for key, value in _BUILTIN_MOTIONS.items() if key.startswith(motion_id)
    ]
    custom_matches = [
        item for item in scene.get("motions", []) if str(item.get("id", "")).startswith(motion_id)
    ]
    if len(builtin_matches) + len(custom_matches) != 1:
        raise ValueError(f"motion not found or ambiguous: {motion_id}")
    if builtin_matches:
        key, value = builtin_matches[0]
        return {"id": key, "builtin": True, **copy.deepcopy(value), "warnings": []}
    return {"builtin": False, **copy.deepcopy(custom_matches[0]), "warnings": []}


def read_camera_path(scene: dict[str, Any], path_id: str) -> dict[str, Any]:
    tracks = [
        item
        for item in scene.get("timeline", {}).get("tracks", [])
        if item.get("type") == "camera_path"
    ]
    path = _find(tracks, path_id)
    return {
        "id": path["id"],
        "label": path.get("name"),
        "durationSec": path.get("durationSec"),
        "easing": path.get("easing", "easeInOut"),
        "loopMode": path.get("loopMode", "once"),
        "cameraId": path.get("entityId"),
        "lookAt": path.get("lookAt"),
        "points": copy.deepcopy(path.get("points", [])),
        "source": path.get("source"),
    }


def diagnose_scene(scene: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    entities = scene.get("entities", [])
    if not any(item.get("type") == "camera" for item in entities):
        issues.append({"kind": "missing_camera", "severity": "error", "detail": "场景没有相机"})
    if not any(item.get("type") == "character" for item in entities):
        issues.append(
            {"kind": "missing_character", "severity": "warning", "detail": "场景没有角色"}
        )
    ids = [str(item.get("id") or "") for item in entities]
    if len(ids) != len(set(ids)):
        issues.append(
            {"kind": "duplicate_entity_id", "severity": "error", "detail": "实体 ID 重复"}
        )
    entity_ids = set(ids)
    for track in scene.get("timeline", {}).get("tracks", []):
        if track.get("entityId") and track["entityId"] not in entity_ids:
            issues.append(
                {
                    "kind": "orphan_track",
                    "severity": "error",
                    "trackId": track.get("id"),
                    "detail": "时间线引用了不存在的实体",
                }
            )
    return {
        "ok": True,
        "clean": not any(item["severity"] == "error" for item in issues),
        "issues": issues,
        "summary": scene_view(scene),
    }


def _apply(scene: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    kind = str(operation.get("type") or "")
    entities = scene.setdefault("entities", [])
    if kind == "set_scene":
        for key in ("name", "scale", "position", "rotation", "skyColor", "background"):
            if key in operation:
                scene.setdefault("scene", {})[key] = operation[key]
        return {"sceneId": scene["id"]}
    if kind in {"add_character", "add_camera"}:
        entity_type = "character" if kind == "add_character" else "camera"
        entity_id = f"{entity_type}-{uuid4().hex[:10]}"
        if entity_type == "character":
            entity = {
                "id": entity_id,
                "type": entity_type,
                "name": str(operation.get("name") or "角色"),
                "bodyType": str(operation.get("bodyType") or "mannequin"),
                "pose": "stand",
                "position": [0, 0, 0],
                "rotation": [0, 0, 0],
                "scale": [1, 1, 1],
            }
        else:
            entity = {
                "id": entity_id,
                "type": entity_type,
                "name": str(operation.get("name") or "相机"),
                "position": [0, 1.6, 5],
                "target": [0, 1, 0],
                "focalLength": 50,
                "aspectRatio": "16:9",
            }
        entities.append(entity)
        return {"entityId": entity_id}
    if kind == "add_prop":
        asset_id = str(operation.get("assetId") or "")
        preset = _PROP_CATALOG.get(asset_id)
        if preset is None:
            raise ValueError(f"unsupported prop asset: {asset_id}")
        uniform_scale = float(operation.get("uniformScale") or 1)
        if not math.isfinite(uniform_scale) or uniform_scale <= 0 or uniform_scale > 20:
            raise ValueError("uniformScale must be within (0, 20]")
        entity_id = f"prop-{uuid4().hex[:10]}"
        size = [float(value) * uniform_scale for value in preset["size"]]
        position = _vector(operation.get("position", [0, 0, 0]), "position")
        entity = {
            "id": entity_id,
            "type": "prop",
            "name": str(operation.get("label") or asset_id.replace("_", " "))[:40],
            "assetId": asset_id,
            "shape": preset["shape"],
            "size": size,
            "color": preset["color"],
            "position": position,
            "rotation": [0, float(operation.get("rotationY") or 0), 0],
            "scale": [1, 1, 1],
        }
        entities.append(entity)
        return {"entityId": entity_id, "assetId": asset_id}
    if kind == "generate_model":
        model_id = str(operation.get("modelId") or f"model-{uuid4().hex[:10]}")
        matches = [item for item in entities if str(item.get("id", "")).startswith(model_id)]
        if len(matches) > 1:
            raise ValueError(f"model not found or ambiguous: {model_id}")
        if matches and matches[0].get("type") != "model":
            raise ValueError("entity is not a model")
        raw_parts = operation.get("parts")
        if not isinstance(raw_parts, list) or not 1 <= len(raw_parts) <= 64:
            raise ValueError("parts must contain 1-64 primitives")
        parts = [_model_part(item, index) for index, item in enumerate(raw_parts)]
        bbox = _model_bbox(parts)
        model = matches[0] if matches else {"id": model_id, "type": "model"}
        model.update(
            {
                "name": str(operation.get("label") or model.get("name") or "程序化模型")[:40],
                "position": _vector(
                    operation.get("position", model.get("position", [0, 0, 0])), "position"
                ),
                "rotation": _vector(
                    operation.get("rotation", model.get("rotation", [0, 0, 0])), "rotation"
                ),
                "scale": _vector(operation.get("scale", model.get("scale", [1, 1, 1])), "scale"),
                "parts": parts,
                "bbox": bbox,
            }
        )
        if not matches:
            entities.append(model)
        warnings = _model_warnings(parts, bbox)
        return {
            "modelId": model["id"],
            "replaced": bool(matches),
            "parts": [part["name"] for part in parts],
            "partDetails": parts,
            "bbox": bbox,
            "warnings": warnings,
        }
    if kind in {"remove_entity", "remove"}:
        entity = _find(entities, operation.get("entityId") or operation.get("id"))
        entities.remove(entity)
        tracks = scene.setdefault("timeline", {}).setdefault("tracks", [])
        scene["timeline"]["tracks"] = [t for t in tracks if t.get("entityId") != entity["id"]]
        return {"entityId": entity["id"]}
    if kind in {"set_transform", "set_pose", "set_camera"}:
        entity = _find(entities, operation.get("entityId") or operation.get("id"))
        if kind == "set_transform":
            for key in ("position", "rotation", "scale"):
                if key in operation:
                    entity[key] = _vector(operation[key], key)
        elif kind == "set_pose":
            if entity.get("type") != "character":
                raise ValueError("entity is not a character")
            pose = str(operation.get("pose") or "")
            if pose not in _POSES:
                raise ValueError("unsupported pose")
            entity["pose"] = pose
            if "bodyType" in operation:
                body_type = str(operation["bodyType"])
                if body_type not in {"mannequin", "female", "child"}:
                    raise ValueError("unsupported body type")
                entity["bodyType"] = body_type
        else:
            if entity.get("type") != "camera":
                raise ValueError("entity is not a camera")
            for key in ("position", "target"):
                if key in operation:
                    entity[key] = _vector(operation[key], key)
            for key in ("focalLength", "aspectRatio"):
                if key in operation:
                    entity[key] = operation[key]
        return {"entityId": entity["id"]}
    if kind == "rename":
        entity = _find(entities, operation.get("entityId") or operation.get("id"))
        label = str(operation.get("label") or "").strip()
        if not label:
            raise ValueError("label is required")
        entity["name"] = label[:40]
        return {"entityId": entity["id"]}
    if kind == "set_environment":
        target = scene.setdefault("scene", {})
        if "skyColor" in operation:
            target["skyColor"] = str(operation["skyColor"])
        if "backgroundMode" in operation:
            mode = str(operation["backgroundMode"])
            if mode not in {"flat", "panorama"}:
                raise ValueError("backgroundMode must be flat or panorama")
            target["backgroundMode"] = mode
        for key in ("showGround", "groundOpacity", "groundHeight"):
            if key in operation:
                target[key] = operation[key]
        if "backgroundImage" in operation:
            image = operation["backgroundImage"]
            if image is not None:
                image = str(image)
                if not image.startswith("data:image/") or len(image) > 4_000_000:
                    raise ValueError("backgroundImage must be a data image under 4 MB")
            target["backgroundImage"] = image
        if "backgroundImageName" in operation:
            target["backgroundImageName"] = str(operation["backgroundImageName"] or "")[:120]
        if "horizontalRotation" in operation:
            target["horizontalRotation"] = max(
                -180.0, min(180.0, float(operation["horizontalRotation"]))
            )
        if "sphereRadius" in operation:
            target["sphereRadius"] = max(30.0, min(180.0, float(operation["sphereRadius"])))
        if "showRoleLabels" in operation:
            target["showRoleLabels"] = bool(operation["showRoleLabels"])
        return {"sceneId": scene["id"]}
    if kind == "add_camera_path":
        camera = _find(entities, operation["cameraId"])
        if camera.get("type") != "camera":
            raise ValueError("entity is not a camera")
        points = operation.get("points")
        if not isinstance(points, list) or len(points) < 2:
            raise ValueError("camera path requires at least two points")
        duration = max(0.1, float(operation.get("durationSec") or 5))
        track = {
            "id": f"track-camera-{uuid4().hex[:10]}",
            "type": "camera_path",
            "entityId": camera["id"],
            "name": str(operation.get("name") or "相机路径"),
            "startSec": max(0, float(operation.get("startSec") or 0)),
            "durationSec": duration,
            "points": [_vector(point, "point") for point in points],
            "easing": str(operation.get("easing") or "easeInOut"),
            "loopMode": str(operation.get("loopMode") or "once"),
            "lookAt": operation.get("lookAt"),
            "source": operation.get("source"),
        }
        timeline = scene.setdefault("timeline", {"durationSec": 0, "tracks": []})
        timeline.setdefault("tracks", []).append(track)
        timeline["durationSec"] = max(
            float(timeline.get("durationSec") or 0), track["startSec"] + duration
        )
        return {"trackId": track["id"]}
    if kind == "set_camera_path":
        tracks = scene.setdefault("timeline", {}).setdefault("tracks", [])
        path = _find(tracks, operation["pathId"])
        if path.get("type") != "camera_path":
            raise ValueError("track is not a camera path")
        if "points" in operation:
            points = operation["points"]
            if not isinstance(points, list) or len(points) < 2:
                raise ValueError("camera path requires at least two points")
            path["points"] = [_vector(point, "point") for point in points]
        for key in ("name", "easing", "loopMode", "lookAt", "source"):
            if key in operation:
                path[key] = operation[key]
        if "durationSec" in operation:
            path["durationSec"] = max(0.1, float(operation["durationSec"]))
        return {"trackId": path["id"]}
    if kind == "add_move_path":
        target = _find(entities, operation.get("targetId") or operation.get("entityId"))
        if target.get("type") not in {"character", "prop", "model"}:
            raise ValueError("move path target must be a character, prop, or model")
        points = operation.get("points")
        if not isinstance(points, list) or len(points) < 2:
            raise ValueError("move path requires at least two points")
        start = max(0.0, float(operation.get("startSec") or 0))
        duration = max(0.1, float(operation.get("durationSec") or 5))
        orient = str(operation.get("orient") or "follow")
        if orient not in {"follow", "keep"}:
            raise ValueError("orient must be follow or keep")
        track = {
            "id": f"track-move-{uuid4().hex[:10]}",
            "type": "object_path",
            "entityId": target["id"],
            "name": str(operation.get("name") or "移动路径")[:40],
            "startSec": start,
            "durationSec": duration,
            "points": [_vector(point, "point") for point in points],
            "orient": orient,
        }
        timeline = scene.setdefault("timeline", {"durationSec": 0, "tracks": []})
        timeline.setdefault("tracks", []).append(track)
        timeline["durationSec"] = max(float(timeline.get("durationSec") or 0), start + duration)
        return {"trackId": track["id"], "entityId": target["id"]}
    if kind == "set_motion":
        motions = scene.setdefault("motions", [])
        motion_id = str(operation.get("motionId") or f"motion-{uuid4().hex[:10]}")
        source = str(operation.get("source") or "").strip()
        if not source:
            raise ValueError("motion source is required")
        matches = [item for item in motions if str(item.get("id", "")).startswith(motion_id)]
        if len(matches) > 1:
            raise ValueError(f"motion not found or ambiguous: {motion_id}")
        motion = matches[0] if matches else {"id": motion_id}
        motion.update(
            {
                "label": str(operation.get("label") or motion.get("label") or "自定义动作"),
                "loop": bool(operation.get("loop", motion.get("loop", False))),
                "cycleMs": max(1, int(operation.get("cycleMs") or motion.get("cycleMs") or 1000)),
                "defaultMs": max(
                    1, int(operation.get("defaultMs") or motion.get("defaultMs") or 1000)
                ),
                "source": source,
            }
        )
        if not matches:
            motions.append(motion)
        return {"motionId": motion["id"]}
    if kind == "remove_motion":
        motions = scene.setdefault("motions", [])
        motion = _find(motions, operation["motionId"])
        motions.remove(motion)
        for track in scene.setdefault("timeline", {}).setdefault("tracks", []):
            if track.get("motionId") == motion["id"]:
                track["enabled"] = False
        return {"motionId": motion["id"]}
    if kind == "add_animation_clip":
        character = _find(entities, operation["characterId"])
        if character.get("type") != "character":
            raise ValueError("entity is not a character")
        motion = read_motion(scene, str(operation["motionId"]))
        start = max(0.0, float(operation.get("startSec") or 0))
        duration = max(0.01, float(operation.get("durationSec") or motion["defaultMs"] / 1000))
        track = {
            "id": f"track-animation-{uuid4().hex[:10]}",
            "type": "character_animation",
            "entityId": character["id"],
            "motionId": motion["id"],
            "name": str(operation.get("name") or motion["label"]),
            "startSec": start,
            "durationSec": duration,
            "enabled": True,
        }
        timeline = scene.setdefault("timeline", {"durationSec": 0, "tracks": []})
        timeline.setdefault("tracks", []).append(track)
        timeline["durationSec"] = max(float(timeline.get("durationSec") or 0), start + duration)
        return {"trackId": track["id"]}
    if kind == "remove_track":
        tracks = scene.setdefault("timeline", {}).setdefault("tracks", [])
        track = _find(tracks, operation["trackId"])
        tracks.remove(track)
        return {"trackId": track["id"]}
    raise ValueError(f"unsupported operation: {kind}")


def _vector(value: Any, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label} must contain three numbers")
    result = [float(item) for item in value]
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{label} must contain finite numbers")
    return result


def _model_part(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"part {index} must be an object")
    shape = str(raw.get("shape") or "box")
    if shape not in {"box", "sphere", "cylinder", "cone"}:
        raise ValueError(f"unsupported primitive shape: {shape}")
    size = _vector(raw.get("size", [1, 1, 1]), "size")
    if any(value <= 0 or value > 100 for value in size):
        raise ValueError("part size must be within (0, 100]")
    color = str(raw.get("color") or "#8b95a7")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("part color must be #RRGGBB")
    return {
        "id": str(raw.get("id") or f"part-{index + 1}"),
        "name": str(raw.get("name") or f"部件 {index + 1}")[:40],
        "shape": shape,
        "size": size,
        "position": _vector(raw.get("position", [0, size[1] / 2, 0]), "position"),
        "rotation": _vector(raw.get("rotation", [0, 0, 0]), "rotation"),
        "color": color.lower(),
        "metalness": max(0.0, min(1.0, float(raw.get("metalness") or 0))),
        "roughness": max(0.0, min(1.0, float(raw.get("roughness", 0.55)))),
    }


def _model_bbox(parts: list[dict[str, Any]]) -> dict[str, list[float]]:
    minimum = [float("inf")] * 3
    maximum = [float("-inf")] * 3
    for part in parts:
        for axis in range(3):
            half = float(part["size"][axis]) / 2
            minimum[axis] = min(minimum[axis], float(part["position"][axis]) - half)
            maximum[axis] = max(maximum[axis], float(part["position"][axis]) + half)
    return {
        "min": minimum,
        "max": maximum,
        "center": [(minimum[i] + maximum[i]) / 2 for i in range(3)],
        "size": [maximum[i] - minimum[i] for i in range(3)],
    }


def _model_warnings(
    parts: list[dict[str, Any]], bbox: dict[str, list[float]]
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if bbox["min"][1] < -0.001:
        warnings.append({"code": "UNDERGROUND", "detail": "模型部件穿入地面"})
    if bbox["min"][1] > 0.05:
        warnings.append({"code": "FLOATING", "detail": "模型没有接触地面"})
    names = [str(part["name"]) for part in parts]
    if len(names) != len(set(names)):
        warnings.append({"code": "DUPLICATE_PART_NAME", "detail": "部件名称重复"})
    return warnings


def _find(items: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
    matches = [item for item in items if str(item.get("id", "")).startswith(str(item_id))]
    if len(matches) != 1:
        raise ValueError(f"entity or track not found or ambiguous: {item_id}")
    return matches[0]


def _path(root: Path, scene_id: str) -> Path:
    if not _SAFE_ID.fullmatch(scene_id):
        raise ValueError("invalid scene id")
    return root / f"{scene_id}.json"


def _without_history(scene: dict[str, Any]) -> dict[str, Any]:
    snapshot = copy.deepcopy(scene)
    snapshot.pop("history", None)
    return snapshot


__all__ = [
    "PROP_CATALOG",
    "diagnose_scene",
    "edit_scene",
    "load_scene",
    "new_scene",
    "read_camera_path",
    "read_motion",
    "save_scene",
    "scene_view",
    "step_history",
]
