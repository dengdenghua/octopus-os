"""Render inspectable timeline frames with the project's existing media stack."""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any
from uuid import uuid4

import av
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


def render_project_frames(
    project: dict[str, Any],
    output_dir: Path,
    *,
    times: list[float],
    max_dim: int = 640,
) -> dict[str, Any]:
    if not 1 <= len(times) <= 8:
        raise ValueError("times must contain 1-8 values")
    max_dim = max(160, min(1280, int(max_dim)))
    output_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for at_sec in times:
        at_sec = max(0.0, float(at_sec))
        image, clip, frame_warnings = render_composite_frame(project, at_sec, max_dim)
        warnings.extend({"atSec": at_sec, **item} for item in frame_warnings)
        path = output_dir / f"frame-{at_sec:.3f}-{uuid4().hex[:8]}.png"
        image.save(path, format="PNG", optimize=True)
        frames.append(
            {
                "atSec": at_sec,
                "path": str(path.resolve()),
                "width": image.width,
                "height": image.height,
                "clipId": clip.get("id"),
                "mediaId": clip.get("mediaId"),
            }
        )
    return {"ok": True, "frames": frames, "warnings": warnings}


def render_composite_frame(
    project: dict[str, Any], at_sec: float, max_dim: int = 640
) -> tuple[Image.Image, dict[str, Any], list[dict[str, Any]]]:
    """Render every visible video layer at a timeline position.

    Track order is bottom-to-top. A solo video track suppresses other video
    tracks. Clip transform/keyframe values are evaluated before alpha/blend
    compositing, then active text tracks are drawn over the result.
    """

    video_tracks = [
        track
        for track in project.get("tracks", [])
        if track.get("type") == "video" and not track.get("hidden")
    ]
    solo = any(track.get("solo") for track in video_tracks)
    if solo:
        video_tracks = [track for track in video_tracks if track.get("solo")]
    active: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for track in video_tracks:
        clips = sorted(
            (
                clip
                for clip in track.get("clips", [])
                if float(clip.get("startSec") or 0) <= at_sec < float(clip.get("endSec") or 0)
            ),
            key=lambda item: float(item.get("startSec") or 0),
        )
        if clips:
            active.append((track, clips[-1]))
    if not active:
        raise ValueError(f"timeline gap at {at_sec:.3f}s")

    settings = project.get("settings", {})
    width = max(1, int(settings.get("width") or 1920))
    height = max(1, int(settings.get("height") or 1080))
    scale = min(1.0, max(160, min(1280, int(max_dim))) / max(width, height))
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    composite = Image.new("RGB", target, "black")
    warnings: list[dict[str, Any]] = []
    top_clip = active[-1][1]
    for _track, clip in active:
        layer = _render_clip(project, clip, at_sec, max_dim)
        layer, transition_warnings = _apply_transition(layer, project, clip, at_sec, max_dim)
        warnings.extend(transition_warnings)
        layer = _apply_transform(layer, clip, at_sec, target)
        composite = _blend_layer(composite, layer, clip, at_sec)
    return _draw_active_text(composite, project, at_sec), top_clip, warnings


def sample_times(
    project: dict[str, Any],
    *,
    times: list[float] | None = None,
    from_sec: float | None = None,
    to_sec: float | None = None,
    count: int = 4,
) -> list[float]:
    if times and (from_sec is not None or to_sec is not None):
        raise ValueError("pass times or a range, not both")
    if times:
        if not 1 <= len(times) <= 8:
            raise ValueError("times must contain 1-8 values")
        return [max(0.0, float(value)) for value in times]
    start = max(0.0, float(from_sec or 0))
    duration = float(project.get("timelineDurationSec") or 0)
    if not duration:
        duration = max(
            (
                float(clip.get("endSec") or 0)
                for track in project.get("tracks", [])
                for clip in track.get("clips", [])
            ),
            default=0.0,
        )
    end = float(to_sec if to_sec is not None else duration)
    if end < start:
        raise ValueError("toSec must be greater than or equal to fromSec")
    count = max(1, min(8, int(count)))
    if count == 1 or math.isclose(start, end):
        return [start]
    return [start + (end - start) * index / (count - 1) for index in range(count)]


def _base_frame(project: dict[str, Any], at_sec: float) -> tuple[Image.Image, dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    for track in project.get("tracks", []):
        if track.get("type") != "video" or track.get("hidden"):
            continue
        visible.extend(
            clip
            for clip in track.get("clips", [])
            if float(clip.get("startSec") or 0) <= at_sec < float(clip.get("endSec") or 0)
        )
    if not visible:
        raise ValueError(f"timeline gap at {at_sec:.3f}s")
    clip = visible[-1]
    media = _media(project, str(clip.get("mediaId") or ""))
    path = _media_path(media)
    if media.get("type") == "image" or path.suffix.lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
    }:
        return Image.open(path).convert("RGB"), clip
    offset = at_sec - float(clip.get("startSec") or 0)
    speed = max(0.1, float(clip.get("speed") or 1))
    source_time = float(clip.get("sourceInSec") or 0) + offset * speed
    if clip.get("reverse"):
        source_time = float(clip.get("sourceOutSec") or 0) - offset * speed
    return _decode_video_frame(path, max(0.0, source_time)), clip


def _decode_video_frame(path: Path, at_sec: float) -> Image.Image:
    try:
        with av.open(str(path)) as container:
            stream = next((item for item in container.streams if item.type == "video"), None)
            if stream is None:
                raise ValueError("media has no video stream")
            if stream.time_base:
                container.seek(
                    max(0, int(at_sec / float(stream.time_base))),
                    stream=stream,
                    backward=True,
                )
            selected = None
            for frame in container.decode(stream):
                selected = frame
                frame_time = float(frame.time or 0)
                if frame_time + 1e-6 >= at_sec:
                    break
            if selected is None:
                raise ValueError("no frame decoded")
            return selected.to_image().convert("RGB")
    except (av.error.FFmpegError, OSError) as exc:
        raise ValueError(f"cannot decode media: {path.name}") from exc


def _fit_canvas(image: Image.Image, project: dict[str, Any], max_dim: int) -> Image.Image:
    settings = project.get("settings", {})
    width = max(1, int(settings.get("width") or 1920))
    height = max(1, int(settings.get("height") or 1080))
    scale = min(1.0, max_dim / max(width, height))
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    fitted = ImageOps.contain(image, target, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", target, "black")
    canvas.paste(fitted, ((target[0] - fitted.width) // 2, (target[1] - fitted.height) // 2))
    return canvas


def _apply_look(
    image: Image.Image, clip: dict[str, Any], at_sec: float | None = None
) -> tuple[Image.Image, list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    for effect in clip.get("effects", []):
        kind = str(effect.get("type") or "")
        params = effect.get("params") or {}
        amount = float(params.get("amount", params.get("value", 1)))
        if at_sec is not None:
            amount = _animated_value(
                clip,
                f"effect:{effect.get('id')}:{'amount' if 'amount' in params else 'value'}",
                at_sec,
                amount,
            )
        if kind == "brightness":
            image = ImageEnhance.Brightness(image).enhance(max(0, amount))
        elif kind == "contrast":
            image = ImageEnhance.Contrast(image).enhance(max(0, amount))
        elif kind == "saturation":
            image = ImageEnhance.Color(image).enhance(max(0, amount))
        elif kind == "blur":
            image = image.filter(ImageFilter.GaussianBlur(radius=max(0, amount)))
        elif kind == "sharpen":
            image = ImageEnhance.Sharpness(image).enhance(max(0, amount or 2))
        elif kind == "grain":
            image = _grain(image, min(0.25, max(0, amount / 100 if amount > 1 else amount)))
        elif kind == "temperature":
            image = _temperature_tint(image, amount, 0)
        elif kind == "tint":
            image = _temperature_tint(image, 0, amount)
        elif kind == "hue":
            image = _hue(image, amount)
        elif kind == "motion_blur":
            image = _motion_blur(image, amount)
        elif kind == "radial_blur":
            image = _radial_blur(image, amount)
        elif kind == "vignette":
            image = _vignette(image, amount)
        elif kind == "shadow":
            image = _shadow(image, amount)
        elif kind == "glow":
            image = _glow(image, amount)
        elif kind in {"chromatic_aberration", "chromatic"}:
            image = _chromatic_aberration(image, amount)
        elif kind:
            warnings.append(
                {"kind": "effect_not_rendered", "effect": kind, "clipId": clip.get("id")}
            )
    grading = clip.get("colorGrading") or {}
    temperature = float(grading.get("temperature") or 0)
    tint = float(grading.get("tint") or 0)
    if temperature or tint:
        image = _temperature_tint(image, temperature, tint)
    return image, warnings


def _apply_transition(
    image: Image.Image,
    project: dict[str, Any],
    clip: dict[str, Any],
    at_sec: float,
    max_dim: int,
) -> tuple[Image.Image, list[dict[str, Any]]]:
    active: tuple[dict[str, Any], dict[str, Any], dict[str, Any], float] | None = None
    for track in project.get("tracks", []):
        clips = sorted(track.get("clips", []), key=lambda item: float(item.get("startSec") or 0))
        if clip not in clips:
            continue
        index = clips.index(clip)
        for transition in clip.get("transitions", []):
            duration = max(0.01, float(transition.get("durationSec") or 0.3))
            edge = str(transition.get("edge") or "out")
            if edge == "in":
                progress = (at_sec - float(clip.get("startSec") or 0)) / duration
                if 0 <= progress <= 1 and index > 0:
                    active = (clips[index - 1], clip, transition, progress)
                    break
            else:
                progress = 1 - (float(clip.get("endSec") or 0) - at_sec) / duration
                if 0 <= progress <= 1 and index + 1 < len(clips):
                    active = (clip, clips[index + 1], transition, progress)
                    break
        if active:
            break
    if not active:
        return image, []

    outgoing, incoming, transition, progress = active
    try:
        outgoing_image = (
            image if outgoing is clip else _render_clip(project, outgoing, at_sec, max_dim)
        )
        incoming_image = (
            image if incoming is clip else _render_clip(project, incoming, at_sec, max_dim)
        )
    except ValueError as exc:
        return image, [
            {
                "kind": "transition_source_unavailable",
                "clipId": clip.get("id"),
                "detail": str(exc),
            }
        ]
    kind = str(transition.get("type") or "crossfade").replace("_", "-").lower()
    if kind in {"crossfade", "cross-fade"}:
        return Image.blend(outgoing_image, incoming_image, progress), []
    if kind in {"fade-black", "fade-to-black", "black"}:
        return _fade_through_color(outgoing_image, incoming_image, progress, "black"), []
    if kind in {"fade-white", "fade-to-white", "white"}:
        return _fade_through_color(outgoing_image, incoming_image, progress, "white"), []
    if kind in {"wipe", "wipe-right"}:
        mask = Image.new("L", outgoing_image.size, 0)
        ImageDraw.Draw(mask).rectangle(
            (0, 0, round(outgoing_image.width * progress), outgoing_image.height),
            fill=255,
        )
        return Image.composite(incoming_image, outgoing_image, mask), []
    if kind in {"slide", "slide-in"}:
        frame = outgoing_image.copy()
        x = round(outgoing_image.width * (1 - progress))
        frame.paste(incoming_image, (x, 0))
        return frame, []
    if kind in {"push", "push-left"}:
        frame = Image.new("RGB", outgoing_image.size, "black")
        offset = round(outgoing_image.width * progress)
        frame.paste(outgoing_image, (-offset, 0))
        frame.paste(incoming_image, (outgoing_image.width - offset, 0))
        return frame, []
    if kind in {"zoom", "zoom-in"}:
        zoomed = _center_zoom(outgoing_image, 1 + progress * 0.18)
        return Image.blend(zoomed, incoming_image, progress), []
    return image, [
        {
            "kind": "transition_not_rendered",
            "transition": kind,
            "clipId": clip.get("id"),
        }
    ]


def _render_clip(
    project: dict[str, Any], clip: dict[str, Any], at_sec: float, max_dim: int
) -> Image.Image:
    media = _media(project, str(clip.get("mediaId") or ""))
    path = _media_path(media)
    start = float(clip.get("startSec") or 0)
    end = float(clip.get("endSec") or start)
    duration = max(0.001, end - start)
    local_offset = max(0.0, min(duration - 0.001, at_sec - start))
    speed = max(0.1, float(clip.get("speed") or 1))
    source_time = float(clip.get("sourceInSec") or 0) + local_offset * speed
    if clip.get("reverse"):
        source_time = float(clip.get("sourceOutSec") or 0) - local_offset * speed
    if media.get("type") == "image" or path.suffix.lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
    }:
        frame = Image.open(path).convert("RGB")
    else:
        frame = _decode_video_frame(path, max(0.0, source_time))
    frame = _fit_canvas(frame, project, max_dim)
    frame, _ = _apply_look(frame, clip, at_sec)
    return frame


def _apply_transform(
    image: Image.Image,
    clip: dict[str, Any],
    at_sec: float,
    target: tuple[int, int],
) -> Image.Image:
    transform = clip.get("transform") or {}
    scale = max(
        0.01,
        _animated_value(clip, "scale", at_sec, float(transform.get("scale", 1))),
    )
    rotation = _animated_value(clip, "rotation", at_sec, float(transform.get("rotation", 0)))
    x = _animated_value(clip, "x", at_sec, float(transform.get("x", 0)))
    y = _animated_value(clip, "y", at_sec, float(transform.get("y", 0)))
    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    transformed = image.resize((width, height), Image.Resampling.LANCZOS).convert("RGBA")
    if rotation:
        transformed = transformed.rotate(-rotation, expand=True, resample=Image.Resampling.BICUBIC)
    layer = Image.new("RGBA", target, (0, 0, 0, 0))
    left = round((target[0] - transformed.width) / 2 + x * target[0])
    top = round((target[1] - transformed.height) / 2 + y * target[1])
    layer.alpha_composite(transformed, (left, top))
    return layer


def _blend_layer(
    base: Image.Image, layer: Image.Image, clip: dict[str, Any], at_sec: float
) -> Image.Image:
    transform = clip.get("transform") or {}
    opacity = max(
        0.0,
        min(
            1.0,
            _animated_value(clip, "opacity", at_sec, float(transform.get("opacity", 1))),
        ),
    )
    if opacity < 1:
        alpha = layer.getchannel("A").point(lambda value: round(value * opacity))
        layer.putalpha(alpha)
    blend_mode = str(transform.get("blendMode") or "normal")
    foreground = layer.convert("RGB")
    if blend_mode == "screen":
        blended = ImageChops.screen(base, foreground)
    elif blend_mode == "multiply":
        blended = ImageChops.multiply(base, foreground)
    elif blend_mode == "add":
        blended = ImageChops.add(base, foreground, scale=1.0, offset=0)
    else:
        blended = foreground
    return Image.composite(blended, base, layer.getchannel("A"))


def _animated_value(
    clip: dict[str, Any], property_name: str, at_sec: float, default: float
) -> float:
    frames = sorted(
        clip.get("keyframes", {}).get(property_name, []),
        key=lambda item: float(item.get("atSec") or 0),
    )
    if not frames:
        return default
    if at_sec <= float(frames[0].get("atSec") or 0):
        return float(frames[0].get("value") or 0)
    if at_sec >= float(frames[-1].get("atSec") or 0):
        return float(frames[-1].get("value") or 0)
    for left, right in zip(frames, frames[1:], strict=False):
        left_at = float(left.get("atSec") or 0)
        right_at = float(right.get("atSec") or 0)
        if left_at <= at_sec <= right_at:
            if str(left.get("easing") or "linear") == "hold":
                return float(left.get("value") or 0)
            progress = (at_sec - left_at) / max(1e-9, right_at - left_at)
            easing = str(right.get("easing") or left.get("easing") or "linear")
            if easing == "ease-in":
                progress *= progress
            elif easing == "ease-out":
                progress = 1 - (1 - progress) ** 2
            elif easing == "ease-in-out":
                progress = progress * progress * (3 - 2 * progress)
            start = float(left.get("value") or 0)
            end = float(right.get("value") or 0)
            return start + (end - start) * progress
    return default


def _draw_active_text(image: Image.Image, project: dict[str, Any], at_sec: float) -> Image.Image:
    active = [
        clip
        for track in project.get("tracks", [])
        if track.get("type") == "text" and not track.get("hidden")
        for clip in track.get("clips", [])
        if float(clip.get("startSec") or 0) <= at_sec < float(clip.get("endSec") or 0)
    ]
    if not active:
        return image
    draw = ImageDraw.Draw(image, "RGBA")
    for index, clip in enumerate(active):
        font_size = max(12, round(float(clip.get("fontSizePx") or 56) * image.width / 1920))
        font = _font(font_size, str(clip.get("fontFamily") or ""))
        text = str(clip.get("text") or "")
        bbox = draw.multiline_textbbox((0, 0), text, font=font, align="center", spacing=4)
        text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        position = str(clip.get("position") or "bottom")
        x = (image.width - text_width) / 2
        if position == "top":
            y = image.height * 0.1 + index * (text_height + 10)
        elif position == "center":
            y = (image.height - text_height) / 2 + index * (text_height + 10)
        else:
            y = image.height * 0.84 - text_height - index * (text_height + 10)
        padding = max(5, font_size // 5)
        background = str(clip.get("backgroundColor") or "#000000a6")
        draw.rounded_rectangle(
            (x - padding, y - padding, x + text_width + padding, y + text_height + padding),
            radius=padding,
            fill=_rgba(background, 166),
        )
        draw.multiline_text(
            (x, y),
            text,
            font=font,
            fill=_rgba(str(clip.get("color") or "#ffffff"), 255),
            align="center",
            spacing=4,
            stroke_width=max(0, round(float(clip.get("outlineWidthPx") or 0))),
            stroke_fill=_rgba(str(clip.get("outlineColor") or "#000000"), 255),
        )
    return image


def _media(project: dict[str, Any], media_id: str) -> dict[str, Any]:
    matches = [
        item for item in project.get("media", []) if str(item.get("id", "")).startswith(media_id)
    ]
    if len(matches) != 1:
        raise ValueError(f"media not found or ambiguous: {media_id}")
    return matches[0]


def _media_path(media: dict[str, Any]) -> Path:
    raw = str(media.get("path") or "")
    if not raw:
        raise ValueError("media has no local path")
    path = Path(raw).expanduser()
    candidates = [path] if path.is_absolute() else [Path.cwd() / path]
    resolved = next((item.resolve() for item in candidates if item.is_file()), None)
    if resolved is None:
        raise ValueError(f"media file not found: {path.name}")
    return resolved


def _font(size: int, family: str) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        family,
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _rgba(value: str, default_alpha: int) -> tuple[int, int, int, int]:
    raw = value.lstrip("#")
    try:
        if len(raw) == 8:
            return tuple(int(raw[index : index + 2], 16) for index in range(0, 8, 2))  # type: ignore[return-value]
        if len(raw) == 6:
            rgb = tuple(int(raw[index : index + 2], 16) for index in range(0, 6, 2))
            return (*rgb, default_alpha)
    except ValueError:
        pass
    return (0, 0, 0, default_alpha)


def _temperature_tint(image: Image.Image, temperature: float, tint: float) -> Image.Image:
    r, g, b = image.split()
    temperature = max(-100, min(100, temperature)) / 100
    tint = max(-100, min(100, tint)) / 100
    r = r.point(lambda value: max(0, min(255, value * (1 + 0.18 * temperature))))
    b = b.point(lambda value: max(0, min(255, value * (1 - 0.18 * temperature))))
    g = g.point(lambda value: max(0, min(255, value * (1 + 0.12 * tint))))
    return Image.merge("RGB", (r, g, b))


def _grain(image: Image.Image, amount: float) -> Image.Image:
    if amount <= 0:
        return image
    rng = random.Random(0)
    noise = Image.new("L", image.size)
    noise.putdata([rng.randrange(256) for _ in range(image.width * image.height)])
    noise_rgb = Image.merge("RGB", (noise, noise, noise))
    return Image.blend(image, noise_rgb, amount)


def _hue(image: Image.Image, amount: float) -> Image.Image:
    shift = round((amount / 360 if abs(amount) > 1 else amount) * 255) % 256
    hsv = image.convert("HSV")
    hue, saturation, value = hsv.split()
    hue = hue.point(lambda pixel: (pixel + shift) % 256)
    return Image.merge("HSV", (hue, saturation, value)).convert("RGB")


def _motion_blur(image: Image.Image, amount: float) -> Image.Image:
    distance = max(1, min(24, round(abs(amount) if abs(amount) > 1 else abs(amount) * 12)))
    accumulator = image.convert("RGBA")
    samples = 5
    for index in range(1, samples):
        shifted = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shifted.paste(image, (round(distance * index / samples), 0))
        accumulator = Image.blend(accumulator, shifted, 1 / (index + 1))
    return accumulator.convert("RGB")


def _radial_blur(image: Image.Image, amount: float) -> Image.Image:
    strength = max(0.01, min(0.12, abs(amount) / 100 if abs(amount) > 1 else abs(amount) * 0.08))
    result = image.copy()
    for index in range(1, 5):
        zoomed = _center_zoom(image, 1 + strength * index / 4)
        result = Image.blend(result, zoomed, 1 / (index + 2))
    return result


def _vignette(image: Image.Image, amount: float) -> Image.Image:
    strength = max(0.0, min(1.0, amount / 100 if amount > 1 else amount or 0.45))
    mask = ImageOps.invert(Image.radial_gradient("L").resize(image.size, Image.Resampling.LANCZOS))
    mask = mask.point(lambda value: round(255 - (255 - value) * strength))
    return Image.composite(image, Image.new("RGB", image.size, "black"), mask)


def _shadow(image: Image.Image, amount: float) -> Image.Image:
    strength = max(0.1, min(1.0, amount / 100 if amount > 1 else amount or 0.55))
    inset = max(4, round(min(image.size) * 0.035))
    content = ImageOps.contain(
        image,
        (max(1, image.width - inset * 2), max(1, image.height - inset * 2)),
        Image.Resampling.LANCZOS,
    )
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_box = Image.new("RGBA", content.size, (0, 0, 0, round(170 * strength)))
    shadow_box = shadow_box.filter(ImageFilter.GaussianBlur(max(2, inset // 2)))
    shadow.alpha_composite(shadow_box, (inset + inset // 2, inset + inset // 2))
    shadow.alpha_composite(content.convert("RGBA"), (inset, inset))
    return shadow.convert("RGB")


def _glow(image: Image.Image, amount: float) -> Image.Image:
    strength = max(0.05, min(0.75, amount / 100 if amount > 1 else amount or 0.28))
    blurred = image.filter(ImageFilter.GaussianBlur(max(2, round(min(image.size) * 0.018))))
    screened = ImageChops.screen(image, blurred)
    return Image.blend(image, screened, strength)


def _chromatic_aberration(image: Image.Image, amount: float) -> Image.Image:
    distance = max(1, min(18, round(abs(amount) if abs(amount) > 1 else abs(amount) * 8)))
    red, green, blue = image.split()
    red = ImageChops.offset(red, distance, 0)
    blue = ImageChops.offset(blue, -distance, 0)
    return Image.merge("RGB", (red, green, blue))


def _fade_through_color(
    outgoing: Image.Image,
    incoming: Image.Image,
    progress: float,
    color: str,
) -> Image.Image:
    solid = Image.new("RGB", outgoing.size, color)
    if progress <= 0.5:
        return Image.blend(outgoing, solid, progress * 2)
    return Image.blend(solid, incoming, (progress - 0.5) * 2)


def _center_zoom(image: Image.Image, factor: float) -> Image.Image:
    factor = max(1.0, factor)
    width = max(image.width, round(image.width * factor))
    height = max(image.height, round(image.height * factor))
    scaled = image.resize((width, height), Image.Resampling.LANCZOS)
    left = (width - image.width) // 2
    top = (height - image.height) // 2
    return scaled.crop((left, top, left + image.width, top + image.height))


__all__ = ["render_composite_frame", "render_project_frames", "sample_times"]
