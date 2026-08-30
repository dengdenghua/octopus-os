"""Small, dependency-free project engine for the Echo clip editor."""

from __future__ import annotations

import copy
import json
import re
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from .silence_analysis import detect_silences

_SAFE_ID = re.compile(r"^[a-zA-Z0-9._-]{1,160}$")
_LOCK = threading.RLock()


def new_project(project_id: str) -> dict[str, Any]:
    video_track = _track("video", "视频 1")
    text_track = _track("text", "字幕")
    audio_track = _track("audio", "音频 1")
    video_track["id"] = "track-video-1"
    text_track["id"] = "track-text-1"
    audio_track["id"] = "track-audio-1"
    return {
        "version": 1,
        "id": project_id,
        "settings": {
            "name": "未命名剪辑",
            "width": 1920,
            "height": 1080,
            "frameRate": 30,
        },
        "playheadSec": 0.0,
        "tracks": [video_track, text_track, audio_track],
        "media": [],
        "markers": [],
        "history": {"undo": [], "redo": []},
    }


def _track(kind: str, name: str) -> dict[str, Any]:
    return {
        "id": f"track-{kind}-{uuid4().hex[:8]}",
        "type": kind,
        "name": name,
        "locked": False,
        "hidden": False,
        "muted": False,
        "solo": False,
        "clips": [],
    }


def load_project(root: Path, project_id: str) -> dict[str, Any]:
    path = _path(root, project_id)
    with _LOCK:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return new_project(project_id)
        except (OSError, json.JSONDecodeError):
            return new_project(project_id)
    return payload if isinstance(payload, dict) else new_project(project_id)


def save_project(root: Path, project: dict[str, Any]) -> None:
    path = _path(root, str(project.get("id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with _LOCK:
        temporary.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def project_view(project: dict[str, Any], view: str = "clips") -> dict[str, Any]:
    tracks = project.get("tracks", [])
    clips = [
        {**clip, "trackId": track["id"], "trackName": track["name"]}
        for track in tracks
        for clip in track.get("clips", [])
        if track.get("type") != "text"
    ]
    text_clips = [
        {**clip, "trackId": track["id"], "trackName": track["name"]}
        for track in tracks
        for clip in track.get("clips", [])
        if track.get("type") == "text"
    ]
    duration = max(
        (float(clip.get("endSec") or 0) for track in tracks for clip in track.get("clips", [])),
        default=0.0,
    )
    result: dict[str, Any] = {
        "id": project.get("id"),
        "settings": project.get("settings", {}),
        "durationSec": duration,
        "playheadSec": project.get("playheadSec", 0),
        "counts": {
            "tracks": len(tracks),
            "clips": len(clips) + len(text_clips),
            "media": len(project.get("media", [])),
        },
        "tracks": [
            {
                **{
                    key: track.get(key)
                    for key in ("id", "type", "name", "locked", "hidden", "muted", "solo")
                },
                "clipCount": len(track.get("clips", [])),
            }
            for track in tracks
        ],
        "clips": clips,
        "textClips": text_clips,
    }
    if view == "full":
        result["media"] = project.get("media", [])
        result["markers"] = project.get("markers", [])
    return result


def edit_project(
    project: dict[str, Any], operations: list[dict[str, Any]], *, validate_only: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    draft = copy.deepcopy(project)
    results: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        try:
            detail = _apply_operation(draft, operation)
            results.append({"index": index, "ok": True, **detail})
        except (KeyError, TypeError, ValueError) as exc:
            return project, {
                "ok": False,
                "applied": 0,
                "failed": 1,
                "rolledBack": True,
                "results": [
                    *results,
                    {"index": index, "ok": False, "detail": str(exc)},
                ],
            }
    if validate_only:
        return project, {
            "ok": True,
            "validateOnly": True,
            "applied": 0,
            "failed": 0,
            "rolledBack": False,
            "results": results,
            "summary": project_view(draft, "summary"),
        }
    prior = _without_history(project)
    history = draft.setdefault("history", {"undo": [], "redo": []})
    history["undo"] = [*project.get("history", {}).get("undo", []), prior][-20:]
    history["redo"] = []
    return draft, {
        "ok": True,
        "applied": len(operations),
        "failed": 0,
        "rolledBack": False,
        "results": results,
        "summary": project_view(draft, "summary"),
    }


def step_history(project: dict[str, Any], action: str) -> tuple[dict[str, Any], bool]:
    if action not in {"undo", "redo"}:
        raise ValueError("action must be undo or redo")
    history = project.get("history", {})
    source = list(history.get(action, []))
    if not source:
        return project, False
    restored = copy.deepcopy(source.pop())
    other = "redo" if action == "undo" else "undo"
    restored["history"] = {
        action: source,
        other: [*history.get(other, []), _without_history(project)][-20:],
    }
    return restored, True


def diagnose_project(project: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    media_ids = {str(item.get("id")) for item in project.get("media", [])}
    video_end = max(
        (
            float(clip.get("endSec") or 0)
            for track in project.get("tracks", [])
            if track.get("type") == "video"
            for clip in track.get("clips", [])
        ),
        default=0.0,
    )
    for track in project.get("tracks", []):
        clips = sorted(track.get("clips", []), key=lambda clip: float(clip.get("startSec") or 0))
        if not clips:
            issues.append(
                {
                    "kind": "empty_track",
                    "severity": "warning",
                    "trackId": track.get("id"),
                    "detail": f"{track.get('name', '轨道')}没有片段",
                }
            )
            continue
        for clip in clips:
            duration = float(clip.get("endSec") or 0) - float(clip.get("startSec") or 0)
            if duration < 2 / 30:
                issues.append(
                    {
                        "kind": "tiny_clip",
                        "severity": "warning",
                        "trackId": track.get("id"),
                        "clipId": clip.get("id"),
                        "detail": "片段短于两帧，可能是误剪",
                    }
                )
            media_id = clip.get("mediaId")
            if media_id and str(media_id) not in media_ids:
                issues.append(
                    {
                        "kind": "media_missing",
                        "severity": "error",
                        "trackId": track.get("id"),
                        "clipId": clip.get("id"),
                        "detail": "片段引用的媒体不存在",
                    }
                )
            if track.get("type") == "text" and float(clip.get("endSec") or 0) > video_end:
                issues.append(
                    {
                        "kind": "caption_out_of_video",
                        "severity": "warning",
                        "trackId": track.get("id"),
                        "clipId": clip.get("id"),
                        "detail": "字幕超出视频画面范围",
                    }
                )
        for left, right in zip(clips, clips[1:], strict=False):
            left_end = float(left.get("endSec") or 0)
            right_start = float(right.get("startSec") or 0)
            if right_start < left_end:
                issues.append(
                    {
                        "kind": "clip_overlap",
                        "severity": "error",
                        "trackId": track.get("id"),
                        "fromSec": right_start,
                        "toSec": left_end,
                        "clipIds": [left.get("id"), right.get("id")],
                        "detail": "同一轨道存在片段重叠",
                    }
                )
            elif track.get("type") == "video" and right_start > left_end + 0.001:
                issues.append(
                    {
                        "kind": "timeline_gap",
                        "severity": "error",
                        "trackId": track.get("id"),
                        "fromSec": left_end,
                        "toSec": right_start,
                        "detail": "视频轨道存在黑场间隙",
                    }
                )
    return {
        "ok": True,
        "clean": not any(item["severity"] == "error" for item in issues),
        "issues": issues,
        "summary": project_view(project, "summary"),
    }


def _apply_operation(project: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    kind = str(operation.get("type") or "")
    tracks = project.setdefault("tracks", [])
    if kind == "add_track":
        track_type = str(operation.get("kind") or "video")
        if track_type not in {"video", "audio", "text"}:
            raise ValueError("unsupported track kind")
        count = sum(track.get("type") == track_type for track in tracks) + 1
        track = _track(track_type, f"{_track_label(track_type)} {count}".strip())
        tracks.append(track)
        return {"trackId": track["id"]}
    if kind == "set_track":
        track = _find(tracks, operation["trackId"], "track")
        for field in ("muted", "hidden", "locked", "solo", "name"):
            if field in operation:
                track[field] = operation[field]
        return {"trackId": track["id"]}
    if kind == "remove_track":
        track = _find(tracks, operation["trackId"], "track")
        tracks.remove(track)
        return {"trackId": track["id"]}
    if kind == "import_media":
        media_id = f"media-{uuid4().hex[:10]}"
        path = str(operation.get("path") or "")
        asset_id = str(operation.get("assetId") or "")
        if not path and not asset_id:
            raise ValueError("path or assetId is required")
        media = {
            "id": media_id,
            "name": str(operation.get("name") or Path(path).name or asset_id),
            "type": str(operation.get("mediaType") or _media_type(path)),
            "path": path or None,
            "assetId": asset_id or None,
            "durationSec": float(operation.get("durationSec") or 5),
        }
        project.setdefault("media", []).append(media)
        result: dict[str, Any] = {"mediaId": media_id}
        if operation.get("addToTimeline", True):
            clip = _add_media_clip(project, media, operation)
            result["clipId"] = clip["id"]
        return result
    if kind == "add_clip":
        media = _find(project.get("media", []), operation["mediaId"], "media")
        clip = _add_media_clip(project, media, operation)
        return {"clipId": clip["id"]}
    if kind == "add_text":
        track = _compatible_track(tracks, "text", operation.get("trackId"))
        start = float(operation.get("atSec") or 0)
        duration = max(0.01, float(operation.get("durationSec") or 3))
        clip = {
            "id": f"text-{uuid4().hex[:10]}",
            "name": "字幕",
            "text": str(operation.get("text") or ""),
            "startSec": start,
            "endSec": start + duration,
            "durationSec": duration,
            "fontSizePx": float(operation.get("fontSizePx") or 56),
        }
        track["clips"].append(clip)
        return {"clipId": clip["id"]}
    if kind == "import_srt":
        content = str(operation.get("content") or "")
        if not content.strip():
            raise ValueError("content is required")
        track = _compatible_track(tracks, "text", operation.get("trackId"))
        offset = float(operation.get("offsetSec") or 0)
        created: list[str] = []
        for start, end, text in _parse_srt(content):
            clip = {
                "id": f"text-{uuid4().hex[:10]}",
                "name": "字幕",
                "text": text,
                "startSec": max(0.0, start + offset),
                "endSec": max(0.01, end + offset),
                "durationSec": max(0.01, end - start),
                **copy.deepcopy(project.get("subtitleStyle", {})),
            }
            clip["durationSec"] = clip["endSec"] - clip["startSec"]
            track["clips"].append(clip)
            created.append(clip["id"])
        return {"trackId": track["id"], "createdClipIds": created, "count": len(created)}
    if kind == "set_subtitle_style":
        allowed = {
            "fontFamily",
            "fontSizePx",
            "color",
            "backgroundColor",
            "position",
            "preset",
            "outlineColor",
            "outlineWidthPx",
        }
        style = {key: operation[key] for key in allowed if key in operation}
        if not style:
            raise ValueError("subtitle style fields are required")
        project.setdefault("subtitleStyle", {}).update(style)
        updated = 0
        if operation.get("applyToExisting", True):
            for track in tracks:
                if track.get("type") != "text":
                    continue
                for clip in track.get("clips", []):
                    clip.update(style)
                    updated += 1
        return {"updatedClipCount": updated, "style": project["subtitleStyle"]}
    if kind in {"set_text", "remove_text_clip"}:
        track, clip = _find_clip(project, operation["clipId"])
        if track.get("type") != "text":
            raise ValueError("clip is not text")
        if kind == "remove_text_clip":
            track["clips"].remove(clip)
        else:
            clip["text"] = str(operation.get("text") or "")
        return {"clipId": clip["id"]}
    if kind == "remove_range":
        start = float(operation["fromSec"])
        end = float(operation["toSec"])
        if end <= start:
            raise ValueError("toSec must be greater than fromSec")
        selected = set(map(str, operation.get("trackIds") or []))
        affected = 0
        for track in tracks:
            if track.get("locked") or (selected and str(track.get("id")) not in selected):
                continue
            affected += _remove_range_from_track(track, start, end, bool(operation.get("ripple")))
        return {"fromSec": start, "toSec": end, "affectedClipCount": affected}
    if kind == "cut_silences":
        track, clip = _find_clip(project, operation["clipId"])
        if track.get("type") not in {"video", "audio"}:
            raise ValueError("clip has no analyzable audio")
        media = _find(project.get("media", []), clip["mediaId"], "media")
        path = _local_media_path(media)
        speed = max(0.1, float(clip.get("speed") or 1))
        source_start = float(clip.get("sourceInSec") or 0)
        source_end = float(
            clip.get("sourceOutSec")
            or source_start
            + (float(clip.get("endSec") or 0) - float(clip.get("startSec") or 0)) * speed
        )
        silences = detect_silences(
            path,
            source_start=source_start,
            source_end=source_end,
            threshold_db=float(operation.get("thresholdDb", -40)),
            min_silence_sec=float(operation.get("minSilenceSec", 0.5)),
            pad_sec=float(operation.get("padSec", 0.1)),
        )
        timeline_ranges = [
            (
                float(clip["startSec"]) + (start - source_start) / speed,
                float(clip["startSec"]) + (end - source_start) / speed,
            )
            for start, end in silences
        ]
        for start, end in reversed(timeline_ranges):
            _remove_range_from_track(track, start, end, True)
        return {
            "clipId": clip["id"],
            "removedSec": sum(end - start for start, end in timeline_ranges),
            "ranges": [{"fromSec": start, "toSec": end} for start, end in timeline_ranges],
        }
    if kind in {
        "move_clip",
        "trim_clip",
        "remove_clip",
        "split_clip",
        "set_clip",
        "set_speed",
        "duplicate_clip",
        "close_gap",
        "add_transition",
        "remove_transition",
        "add_effect",
        "remove_effect",
        "set_color_grading",
        "set_clip_transform",
        "set_keyframe",
        "remove_keyframe",
    }:
        track, clip = _find_clip(project, operation["clipId"])
        if kind == "remove_clip":
            start = float(clip["startSec"])
            duration = float(clip["endSec"]) - start
            track["clips"].remove(clip)
            if operation.get("ripple"):
                _shift_clips(track, start, -duration)
        elif kind == "move_clip":
            duration = float(clip["endSec"]) - float(clip["startSec"])
            clip["startSec"] = float(operation["toSec"])
            clip["endSec"] = clip["startSec"] + duration
            if operation.get("trackId") and operation["trackId"] != track["id"]:
                destination = _find(tracks, operation["trackId"], "track")
                track["clips"].remove(clip)
                destination["clips"].append(clip)
        elif kind == "trim_clip":
            if "newStartSec" in operation:
                clip["startSec"] = float(operation["newStartSec"])
            if "newEndSec" in operation:
                clip["endSec"] = float(operation["newEndSec"])
            if float(clip["endSec"]) <= float(clip["startSec"]):
                raise ValueError("trim creates an empty clip")
            clip["durationSec"] = float(clip["endSec"]) - float(clip["startSec"])
        elif kind == "split_clip":
            at = float(operation["atSec"])
            if not float(clip["startSec"]) < at < float(clip["endSec"]):
                raise ValueError("split point is outside the clip")
            clone = copy.deepcopy(clip)
            clone["id"] = f"clip-{uuid4().hex[:10]}"
            clone["startSec"] = at
            clone["durationSec"] = float(clone["endSec"]) - at
            clip["endSec"] = at
            clip["durationSec"] = at - float(clip["startSec"])
            track["clips"].append(clone)
            return {"clipId": clip["id"], "createdClipId": clone["id"]}
        elif kind == "duplicate_clip":
            clone = copy.deepcopy(clip)
            clone["id"] = _new_clip_id(track)
            duration = float(clip["endSec"]) - float(clip["startSec"])
            start = float(operation.get("atSec", float(clip["endSec"])))
            clone["startSec"] = start
            clone["endSec"] = start + duration
            destination = track
            if operation.get("trackId") and operation["trackId"] != track["id"]:
                destination = _find(tracks, operation["trackId"], "track")
                if destination.get("type") != track.get("type"):
                    raise ValueError("track type is incompatible")
            destination["clips"].append(clone)
            return {"clipId": clip["id"], "createdClipId": clone["id"]}
        elif kind == "close_gap":
            start = float(clip["startSec"])
            previous_end = max(
                (
                    float(other.get("endSec") or 0)
                    for other in track.get("clips", [])
                    if other is not clip and float(other.get("endSec") or 0) <= start
                ),
                default=0.0,
            )
            gap = max(0.0, start - previous_end)
            _shift_clips(track, start, -gap)
            return {"clipId": clip["id"], "closedGapSec": gap}
        elif kind == "add_transition":
            transition = {
                "id": f"transition-{uuid4().hex[:10]}",
                "type": str(operation.get("transitionType") or "crossfade"),
                "durationSec": max(0.01, float(operation.get("durationSec") or 0.3)),
                "edge": str(operation.get("edge") or "out"),
            }
            clip.setdefault("transitions", []).append(transition)
            return {"clipId": clip["id"], "transitionId": transition["id"]}
        elif kind == "remove_transition":
            transition = _find(
                clip.setdefault("transitions", []), operation["transitionId"], "transition"
            )
            clip["transitions"].remove(transition)
            return {"clipId": clip["id"], "transitionId": transition["id"]}
        elif kind == "add_effect":
            effect = {
                "id": f"effect-{uuid4().hex[:10]}",
                "type": str(operation.get("effectType") or "blur"),
                "params": copy.deepcopy(operation.get("params") or {}),
            }
            clip.setdefault("effects", []).append(effect)
            return {"clipId": clip["id"], "effectId": effect["id"]}
        elif kind == "remove_effect":
            effect = _find(clip.setdefault("effects", []), operation["effectId"], "effect")
            clip["effects"].remove(effect)
            return {"clipId": clip["id"], "effectId": effect["id"]}
        elif kind == "set_color_grading":
            clip["colorGrading"] = copy.deepcopy(operation.get("settings") or {})
            return {"clipId": clip["id"], "colorGrading": clip["colorGrading"]}
        elif kind == "set_clip_transform":
            transform = clip.setdefault("transform", {})
            limits = {
                "x": (-4.0, 4.0),
                "y": (-4.0, 4.0),
                "scale": (0.01, 8.0),
                "rotation": (-3600.0, 3600.0),
                "opacity": (0.0, 1.0),
            }
            changed: dict[str, Any] = {}
            for field, (minimum, maximum) in limits.items():
                if field not in operation:
                    continue
                value = max(minimum, min(maximum, float(operation[field])))
                transform[field] = value
                changed[field] = value
            if "blendMode" in operation:
                blend_mode = str(operation["blendMode"])
                if blend_mode not in {"normal", "screen", "multiply", "add"}:
                    raise ValueError("unsupported blend mode")
                transform["blendMode"] = blend_mode
                changed["blendMode"] = blend_mode
            if not changed:
                raise ValueError("transform fields are required")
            return {"clipId": clip["id"], "transform": copy.deepcopy(transform)}
        elif kind == "set_keyframe":
            property_name = str(operation.get("property") or "")
            _validate_keyframe_property(clip, property_name)
            at_sec = float(operation["atSec"])
            if not float(clip["startSec"]) <= at_sec <= float(clip["endSec"]):
                raise ValueError("keyframe is outside the clip")
            value = float(operation["value"])
            easing = str(operation.get("easing") or "linear")
            if easing not in {"linear", "ease-in", "ease-out", "ease-in-out", "hold"}:
                raise ValueError("unsupported keyframe easing")
            keyframes = clip.setdefault("keyframes", {}).setdefault(property_name, [])
            existing = next(
                (item for item in keyframes if abs(float(item.get("atSec") or 0) - at_sec) < 1e-6),
                None,
            )
            payload = {"atSec": at_sec, "value": value, "easing": easing}
            if existing is None:
                keyframes.append(payload)
            else:
                existing.update(payload)
            keyframes.sort(key=lambda item: float(item.get("atSec") or 0))
            return {"clipId": clip["id"], "property": property_name, "keyframe": payload}
        elif kind == "remove_keyframe":
            property_name = str(operation.get("property") or "")
            at_sec = float(operation["atSec"])
            keyframes = clip.setdefault("keyframes", {}).get(property_name, [])
            before = len(keyframes)
            keyframes[:] = [
                item for item in keyframes if abs(float(item.get("atSec") or 0) - at_sec) >= 1e-6
            ]
            if len(keyframes) == before:
                raise ValueError("keyframe not found")
            if not keyframes:
                clip["keyframes"].pop(property_name, None)
            return {"clipId": clip["id"], "property": property_name, "atSec": at_sec}
        elif kind == "set_clip":
            clip["volume"] = max(0, min(2, float(operation.get("volume") or 0)))
        else:
            speed = max(0.1, min(20, float(operation.get("speed") or 1)))
            source_duration = float(clip["endSec"]) - float(clip["startSec"])
            clip["speed"] = speed
            clip["endSec"] = float(clip["startSec"]) + source_duration / speed
            clip["durationSec"] = source_duration / speed
        return {"clipId": clip["id"]}
    if kind == "add_marker":
        marker = {
            "id": f"marker-{uuid4().hex[:10]}",
            "atSec": float(operation.get("atSec") or 0),
            "label": str(operation.get("label") or "标记"),
        }
        project.setdefault("markers", []).append(marker)
        return {"markerId": marker["id"]}
    if kind == "remove_marker":
        marker = _find(project.get("markers", []), operation["markerId"], "marker")
        project["markers"].remove(marker)
        return {"markerId": marker["id"]}
    raise ValueError(f"unsupported operation: {kind}")


def _validate_keyframe_property(clip: dict[str, Any], property_name: str) -> None:
    if property_name in {"x", "y", "scale", "rotation", "opacity"}:
        return
    match = re.fullmatch(r"effect:([a-zA-Z0-9._-]{1,160}):([a-zA-Z0-9._-]{1,80})", property_name)
    if not match:
        raise ValueError("unsupported keyframe property")
    effect_id, _parameter = match.groups()
    _find(clip.get("effects", []), effect_id, "effect")


def _shift_clips(track: dict[str, Any], from_sec: float, delta: float) -> None:
    for clip in track.get("clips", []):
        if float(clip.get("startSec") or 0) >= from_sec:
            clip["startSec"] = max(0.0, float(clip["startSec"]) + delta)
            clip["endSec"] = max(clip["startSec"] + 0.01, float(clip["endSec"]) + delta)


def _remove_range_from_track(track: dict[str, Any], start: float, end: float, ripple: bool) -> int:
    duration = end - start
    updated: list[dict[str, Any]] = []
    affected = 0
    for clip in track.get("clips", []):
        clip_start = float(clip.get("startSec") or 0)
        clip_end = float(clip.get("endSec") or 0)
        if clip_end <= start:
            updated.append(clip)
            continue
        if clip_start >= end:
            if ripple:
                clip["startSec"] -= duration
                clip["endSec"] -= duration
            updated.append(clip)
            continue
        affected += 1
        if clip_start < start and clip_end > end:
            right = copy.deepcopy(clip)
            right["id"] = _new_clip_id(track)
            speed = max(0.1, float(clip.get("speed") or 1))
            source_in = float(clip.get("sourceInSec") or 0)
            clip["endSec"] = start
            clip["durationSec"] = start - clip_start
            if "sourceOutSec" in clip:
                clip["sourceOutSec"] = source_in + clip["durationSec"] * speed
            right["startSec"] = start if ripple else end
            right["durationSec"] = clip_end - end
            right["endSec"] = right["startSec"] + right["durationSec"]
            if "sourceInSec" in right:
                right["sourceInSec"] = source_in + (end - clip_start) * speed
            updated.extend([clip, right])
        elif clip_start < start < clip_end:
            clip["endSec"] = start
            clip["durationSec"] = start - clip_start
            if "sourceOutSec" in clip:
                clip["sourceOutSec"] = float(clip.get("sourceInSec") or 0) + clip[
                    "durationSec"
                ] * max(0.1, float(clip.get("speed") or 1))
            updated.append(clip)
        elif clip_start < end < clip_end:
            if "sourceInSec" in clip:
                clip["sourceInSec"] = float(clip.get("sourceInSec") or 0) + (
                    end - clip_start
                ) * max(0.1, float(clip.get("speed") or 1))
            clip["startSec"] = start if ripple else end
            clip["durationSec"] = clip_end - end
            clip["endSec"] = clip["startSec"] + clip["durationSec"]
            updated.append(clip)
    track["clips"] = updated
    return affected


def _new_clip_id(track: dict[str, Any]) -> str:
    prefix = "text" if track.get("type") == "text" else "clip"
    return f"{prefix}-{uuid4().hex[:10]}"


def _parse_srt(content: str) -> list[tuple[float, float, str]]:
    entries: list[tuple[float, float, str]] = []
    pattern = re.compile(
        r"(?:^|\n)\s*(?:\d+\s*\n)?"
        r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
        r"(\d{2}:\d{2}:\d{2}[,.]\d{3})[^\n]*\n"
        r"(.+?)(?=\n\s*\n|\Z)",
        re.DOTALL,
    )
    for match in pattern.finditer(content.replace("\r\n", "\n")):
        start = _srt_seconds(match.group(1))
        end = _srt_seconds(match.group(2))
        if end <= start:
            raise ValueError("SRT cue end must be after start")
        entries.append(
            (start, end, "\n".join(line.strip() for line in match.group(3).splitlines()))
        )
    if not entries:
        raise ValueError("no valid SRT cues found")
    return entries


def _srt_seconds(value: str) -> float:
    hours, minutes, rest = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def _add_media_clip(
    project: dict[str, Any], media: dict[str, Any], operation: dict[str, Any]
) -> dict[str, Any]:
    track_type = "audio" if media.get("type") == "audio" else "video"
    track = _compatible_track(project["tracks"], track_type, operation.get("trackId"))
    start = float(
        operation["atSec"] if "atSec" in operation else project_view(project)["durationSec"]
    )
    duration = max(0.01, float(media.get("durationSec") or 5))
    clip = {
        "id": f"clip-{uuid4().hex[:10]}",
        "name": media.get("name") or "媒体",
        "mediaId": media["id"],
        "startSec": start,
        "endSec": start + duration,
        "durationSec": duration,
        "sourceInSec": 0,
        "sourceOutSec": duration,
        "volume": 1,
        "effects": [],
    }
    track["clips"].append(clip)
    return clip


def _compatible_track(
    tracks: list[dict[str, Any]], kind: str, track_id: Any = None
) -> dict[str, Any]:
    if track_id:
        track = _find(tracks, str(track_id), "track")
        if track.get("type") != kind:
            raise ValueError("track type is incompatible")
        return track
    for track in tracks:
        if track.get("type") == kind and not track.get("locked"):
            return track
    track = _track(kind, f"{_track_label(kind)} 1".strip())
    tracks.append(track)
    return track


def _find(items: list[dict[str, Any]], item_id: str, label: str) -> dict[str, Any]:
    matches = [item for item in items if str(item.get("id", "")).startswith(str(item_id))]
    if len(matches) != 1:
        raise ValueError(f"{label} not found or ambiguous: {item_id}")
    return matches[0]


def _find_clip(project: dict[str, Any], clip_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    matches = [
        (track, clip)
        for track in project.get("tracks", [])
        for clip in track.get("clips", [])
        if str(clip.get("id", "")).startswith(str(clip_id))
    ]
    if len(matches) != 1:
        raise ValueError(f"clip not found or ambiguous: {clip_id}")
    return matches[0]


def _without_history(project: dict[str, Any]) -> dict[str, Any]:
    snapshot = copy.deepcopy(project)
    snapshot.pop("history", None)
    return snapshot


def _path(root: Path, project_id: str) -> Path:
    if not _SAFE_ID.fullmatch(project_id):
        raise ValueError("invalid project id")
    return root / f"{project_id}.json"


def _track_label(kind: str) -> str:
    return {"video": "视频", "audio": "音频", "text": "字幕"}.get(kind, kind)


def _media_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".mp3", ".wav", ".m4a", ".flac", ".aac"}:
        return "audio"
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return "image"
    return "video"


def _local_media_path(media: dict[str, Any]) -> Path:
    raw = str(media.get("path") or "")
    if not raw:
        raise ValueError("media has no local path")
    path = Path(raw).expanduser()
    candidate = path if path.is_absolute() else Path.cwd() / path
    if not candidate.is_file():
        raise ValueError(f"media file not found: {path.name}")
    return candidate.resolve()


__all__ = [
    "diagnose_project",
    "edit_project",
    "load_project",
    "new_project",
    "project_view",
    "save_project",
    "step_history",
]
