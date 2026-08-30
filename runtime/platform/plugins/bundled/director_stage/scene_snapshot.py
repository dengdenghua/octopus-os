"""Persist the real WebGL preview frame produced by the Director Stage UI."""

from __future__ import annotations

import base64
import binascii
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image


def save_visual_snapshot(
    output_dir: Path, data_url: str, *, view: str = "director"
) -> dict[str, Any]:
    prefix = "data:image/png;base64,"
    if not data_url.startswith(prefix):
        raise ValueError("snapshot must be a PNG data URL")
    try:
        payload = base64.b64decode(data_url[len(prefix) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("snapshot contains invalid base64") from exc
    if not payload or len(payload) > 12 * 1024 * 1024:
        raise ValueError("snapshot must be within 12 MB")
    try:
        image = Image.open(BytesIO(payload))
        image.load()
    except OSError as exc:
        raise ValueError("snapshot is not a valid PNG") from exc
    if image.width < 64 or image.height < 64 or image.width > 4096 or image.height > 4096:
        raise ValueError("snapshot dimensions must be within 64-4096 px")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"latest-{view}.png"
    image.convert("RGB").save(path, format="PNG", optimize=True)
    return {
        "ok": True,
        "visualEvidence": True,
        "view": view,
        "path": str(path.resolve()),
        "width": image.width,
        "height": image.height,
    }


def read_visual_snapshot(output_dir: Path, *, view: str = "director") -> dict[str, Any]:
    path = output_dir / f"latest-{view}.png"
    if not path.is_file():
        raise ValueError("PREVIEW_NOT_READY")
    try:
        with Image.open(path) as image:
            width, height = image.size
    except OSError as exc:
        raise ValueError("PREVIEW_NOT_READY") from exc
    return {
        "ok": True,
        "visualEvidence": True,
        "frames": [
            {
                "view": view,
                "path": str(path.resolve()),
                "width": width,
                "height": height,
            }
        ],
        "detail": "来自导演台当前 WebGL 预览；返回路径后仍需实际查看图片",
    }


__all__ = ["read_visual_snapshot", "save_visual_snapshot"]
