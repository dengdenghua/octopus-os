"""Safe declarative-model captures for Director Stage visual inspection."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageStat

_VIEWS = {"front", "side", "top", "iso"}


def capture_model(
    scene: dict[str, Any],
    model_id: str,
    output_dir: Path,
    *,
    views: list[str] | None = None,
    max_dim: int = 640,
) -> dict[str, Any]:
    model = _model(scene, model_id)
    selected = views or ["front", "side", "iso"]
    if not 1 <= len(selected) <= 4 or any(view not in _VIEWS for view in selected):
        raise ValueError("views must contain 1-4 of front/side/top/iso")
    size = max(240, min(1280, int(max_dim)))
    output_dir.mkdir(parents=True, exist_ok=True)
    captures: list[dict[str, Any]] = []
    for view in selected:
        image = _render_model(
            model, view, size, str(scene.get("scene", {}).get("skyColor") or "#fafafa")
        )
        path = output_dir / f"{model['id']}-{view}-{uuid4().hex[:8]}.png"
        image.save(path, format="PNG", optimize=True)
        captures.append(
            {
                "view": view,
                "path": str(path.resolve()),
                "width": image.width,
                "height": image.height,
            }
        )
    return {
        "ok": True,
        "modelId": model["id"],
        "label": model.get("name"),
        "captures": captures,
        "bbox": model.get("bbox"),
    }


def compare_model(
    scene: dict[str, Any],
    model_id: str,
    reference_path: str,
    output_dir: Path,
    *,
    view: str = "iso",
) -> dict[str, Any]:
    reference = Path(reference_path).expanduser()
    if not reference.is_file():
        raise ValueError("reference image not found")
    capture = capture_model(scene, model_id, output_dir, views=[view], max_dim=640)
    generated_path = Path(capture["captures"][0]["path"])
    generated = Image.open(generated_path).convert("RGB")
    target = Image.open(reference).convert("RGB")
    generated_fit = ImageOps.fit(generated, (256, 256), Image.Resampling.LANCZOS)
    target_fit = ImageOps.fit(target, (256, 256), Image.Resampling.LANCZOS)
    difference = ImageChops.difference(generated_fit, target_fit)
    rms = sum(ImageStat.Stat(difference).rms) / 3
    score = max(0.0, min(1.0, 1 - rms / 255))
    diff_path = output_dir / f"{capture['modelId']}-compare-{uuid4().hex[:8]}.png"
    difference.save(diff_path, format="PNG")
    return {
        "ok": True,
        "modelId": capture["modelId"],
        "view": view,
        "score": round(score, 4),
        "pixelRms": round(rms, 3),
        "capturePath": str(generated_path),
        "referencePath": str(reference.resolve()),
        "differencePath": str(diff_path.resolve()),
        "detail": "像素差异只衡量轮廓、布局与色彩接近度，不代表语义或美术质量",
    }


def _render_model(model: dict[str, Any], view: str, size: int, background: str) -> Image.Image:
    image = Image.new("RGB", (size, size), background)
    draw = ImageDraw.Draw(image, "RGBA")
    bbox = model.get("bbox") or {"size": [1, 1, 1], "center": [0, 0, 0]}
    span = max(0.1, *[float(value) for value in bbox.get("size", [1, 1, 1])])
    scale = size * 0.64 / span
    center = [float(value) for value in bbox.get("center", [0, 0, 0])]
    parts = sorted(model.get("parts", []), key=lambda part: _depth(part, view))
    for part in parts:
        point = [float(value) for value in part.get("position", [0, 0, 0])]
        point = [point[index] - center[index] for index in range(3)]
        width, height = _projected_size(part, view, scale)
        x, y = _project(point, view, scale, size)
        color = str(part.get("color") or "#8b95a7")
        outline = _shade(color, 0.66)
        shape = part.get("shape")
        rect = (x - width / 2, y - height / 2, x + width / 2, y + height / 2)
        if shape == "sphere":
            draw.ellipse(rect, fill=color, outline=outline, width=max(1, size // 320))
            draw.ellipse(
                (x - width * 0.2, y - height * 0.28, x + width * 0.05, y - height * 0.03),
                fill=(255, 255, 255, 70),
            )
        elif shape == "cone":
            draw.polygon(
                [
                    (x, y - height / 2),
                    (x - width / 2, y + height / 2),
                    (x + width / 2, y + height / 2),
                ],
                fill=color,
                outline=outline,
            )
        elif shape == "cylinder":
            cap = max(4, height * 0.18)
            draw.rectangle(
                (x - width / 2, y - height / 2 + cap / 2, x + width / 2, y + height / 2 - cap / 2),
                fill=color,
                outline=outline,
            )
            draw.ellipse(
                (x - width / 2, y - height / 2, x + width / 2, y - height / 2 + cap),
                fill=_shade(color, 1.16),
                outline=outline,
            )
            draw.ellipse(
                (x - width / 2, y + height / 2 - cap, x + width / 2, y + height / 2),
                fill=_shade(color, 0.86),
                outline=outline,
            )
        else:
            radius = max(2, min(width, height) * 0.05)
            draw.rounded_rectangle(
                rect, radius=radius, fill=color, outline=outline, width=max(1, size // 320)
            )
    draw.line((size * 0.1, size * 0.84, size * 0.9, size * 0.84), fill=(40, 50, 65, 60), width=1)
    return image


def _project(point: list[float], view: str, scale: float, size: int) -> tuple[float, float]:
    x, y, z = point
    if view == "front":
        u, v = x, y
    elif view == "side":
        u, v = z, y
    elif view == "top":
        u, v = x, -z
    else:
        u, v = (x - z) * 0.78, y + (x + z) * 0.32
    return size / 2 + u * scale, size * 0.58 - v * scale


def _projected_size(part: dict[str, Any], view: str, scale: float) -> tuple[float, float]:
    x, y, z = [float(value) for value in part.get("size", [1, 1, 1])]
    if view == "front":
        return max(2, x * scale), max(2, y * scale)
    if view == "side":
        return max(2, z * scale), max(2, y * scale)
    if view == "top":
        return max(2, x * scale), max(2, z * scale)
    return max(2, (x + z) * 0.62 * scale), max(2, (y + (x + z) * 0.2) * scale)


def _depth(part: dict[str, Any], view: str) -> float:
    x, _y, z = [float(value) for value in part.get("position", [0, 0, 0])]
    return {"front": z, "side": x, "top": -float(part.get("position", [0, 0, 0])[1])}.get(
        view, x + z
    )


def _shade(color: str, factor: float) -> tuple[int, int, int, int]:
    raw = color.lstrip("#")
    values = [int(raw[index : index + 2], 16) for index in range(0, 6, 2)]
    return tuple(min(255, max(0, round(value * factor))) for value in values) + (255,)  # type: ignore[return-value]


def _model(scene: dict[str, Any], model_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in scene.get("entities", [])
        if item.get("type") == "model" and str(item.get("id", "")).startswith(model_id)
    ]
    if len(matches) != 1:
        raise ValueError(f"model not found or ambiguous: {model_id}")
    return matches[0]


__all__ = ["capture_model", "compare_model"]
