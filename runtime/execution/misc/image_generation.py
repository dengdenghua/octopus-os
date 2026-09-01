from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

AGENT_VISUAL_VIEWS = ("front", "side", "back")
AGENT_VISUAL_METASKILL_ID = "agent-visual-kit"

AGENT_VISUAL_METASKILL_CONSTRAINTS = (
    f"Use the {AGENT_VISUAL_METASKILL_ID} metaskill workflow. "
    "Create an Echo Agent visual asset pack for HUD use: front.png, "
    "side.png, back.png, and a separate replacement avatar.png generated "
    "as a fixed-size close-up headshot. "
    "Assemble the character from identity, role, background, personality, "
    "temperament, apparent age, visual keywords, and user custom additions. "
    "Generate a single full-body character standee with a clear, centered "
    "face suitable for matching the separate large headshot avatar, not an infographic, "
    "profile card, UI panel, poster, or character stat sheet. "
    "Keep the full head, hair, hands, and feet inside the canvas with "
    "generous transparent headroom and footroom. Maintain the same face, "
    "hairstyle, outfit, palette, proportions, and role-readable design "
    "language across front, side, and back views. Prefer true alpha "
    "transparency; if unavailable, use one perfectly flat #00ff00 chroma-key "
    "background for deterministic background removal."
)

AGENT_VISUAL_NEGATIVE_CONSTRAINTS = (
    "No text, no typography, no UI frame, no stat panels, no labels, no logo, "
    "no name card, no poster, no border, no watermark, no duplicated "
    "character, no cropped head, no cropped hair, no cropped hands, no "
    "cropped feet, no half-body crop, no rough sketch, no 3D render, no "
    "busy scenery, no gradient background, no floor, no props outside the "
    "character, no floating particles, no floating code glyphs, no detached "
    "symbols. Any circuitry, terminal, tool, or domain motif must be "
    "integrated into clothing or equipment only."
)


@dataclass(frozen=True)
class AgentVisualResult:
    provider: str
    prompt: str
    files: dict[str, Path]


def build_agent_visual_prompt(
    *,
    agent_id: str,
    display_name: str,
    description: str = "",
    style_prompt: str = "",
) -> str:
    base = (
        f"{AGENT_VISUAL_METASKILL_CONSTRAINTS} "
        "Generate high-resolution 2D game character artwork with anime concept "
        "art / premium RPG character sheet quality, clean readable silhouette, "
        "crisp linework, detailed clothing, polished lighting, sharp eyes, "
        "and no blur. "
        f"{AGENT_VISUAL_NEGATIVE_CONSTRAINTS}"
    )
    identity = f"Agent id: {agent_id}. Agent name: {display_name}."
    if description:
        identity += f" Description: {description[:500]}."
    if style_prompt:
        identity += f" Character/style reference notes and user additions: {style_prompt[:1400]}."
    return f"{base} {identity}"


def generate_agent_visuals(
    *,
    agent_id: str,
    display_name: str,
    description: str,
    output_dir: Path,
    style_prompt: str = "",
    reference_images: list[str] | None = None,
    provider: str | None = None,
) -> AgentVisualResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_provider = (provider or os.getenv("ECHO_IMAGE_GEN_PROVIDER") or "mock").strip()
    prompt = build_agent_visual_prompt(
        agent_id=agent_id,
        display_name=display_name,
        description=description,
        style_prompt=style_prompt,
    )

    if resolved_provider in {"mock", "local-mock"}:
        return _generate_mock_visuals(
            provider=resolved_provider,
            prompt=prompt,
            agent_id=agent_id,
            display_name=display_name,
            output_dir=output_dir,
        )
    if resolved_provider in {"agnes", "agnes-ai", "agnes-image"}:
        return _generate_with_agnes(
            provider=resolved_provider,
            prompt=prompt,
            agent_id=agent_id,
            display_name=display_name,
            output_dir=output_dir,
            reference_images=reference_images,
        )
    if resolved_provider in {"opencli-jimeng", "jimeng-cli", "custom-command"}:
        return _generate_with_command(
            provider=resolved_provider,
            prompt=prompt,
            agent_id=agent_id,
            display_name=display_name,
            output_dir=output_dir,
        )
    raise ValueError(f"unsupported image generation provider: {resolved_provider}")


def _generate_with_agnes(
    *,
    provider: str,
    prompt: str,
    agent_id: str,
    display_name: str,
    output_dir: Path,
    reference_images: list[str] | None = None,
) -> AgentVisualResult:
    agnes_config = _resolve_agnes_config()
    api_key = agnes_config["api_key"]
    if not api_key:
        raise ValueError("AGNES_API_KEY not found")

    base_url = agnes_config["base_url"].rstrip("/")
    clean_reference_images = [
        image.strip() for image in (reference_images or []) if image and image.strip()
    ][:3]
    # agnes-image-2.1-flash supports both text→image and image→image,
    # so we use the same model regardless of whether reference images are present.
    # AGNES_IMAGE_REFERENCE_MODEL remains as an escape hatch for explicit overrides.
    model = os.getenv("AGNES_IMAGE_REFERENCE_MODEL", "").strip() or agnes_config["model"]
    size = os.getenv("AGNES_IMAGE_SIZE", "").strip() or "1024x1536"
    avatar_size = os.getenv("AGNES_AVATAR_IMAGE_SIZE", "").strip() or "512x512"
    timeout = int(os.getenv("ECHO_IMAGE_GEN_TIMEOUT_SECONDS") or "180")

    files: dict[str, Path] = {}
    for view in AGENT_VISUAL_VIEWS:
        view_prompt = _agent_visual_view_prompt(
            base_prompt=prompt,
            view=view,
            agent_id=agent_id,
            display_name=display_name,
        )
        data = _post_agnes_image_generation(
            base_url=base_url,
            api_key=api_key,
            model=model,
            prompt=view_prompt,
            size=size,
            timeout=timeout,
            reference_images=clean_reference_images,
        )
        out = output_dir / f"{view}.png"
        _write_agnes_image_result(data, out, timeout=timeout)
        _postprocess_agent_visual(out)
        files[view] = out

    avatar_path = output_dir.parent / "avatar.png"
    avatar_prompt = _agent_visual_avatar_prompt(
        base_prompt=prompt,
        agent_id=agent_id,
        display_name=display_name,
    )
    try:
        avatar_data = _post_agnes_image_generation(
            base_url=base_url,
            api_key=api_key,
            model=model,
            prompt=avatar_prompt,
            size=avatar_size,
            timeout=timeout,
            reference_images=clean_reference_images,
        )
        _write_agnes_image_result(avatar_data, avatar_path, timeout=timeout)
        _postprocess_avatar_image(avatar_path)
        avatar = avatar_path
    except (OSError, RuntimeError, ValueError):
        avatar = _make_avatar_from_front(output_dir / "front.png", avatar_path)
    if avatar is not None:
        files["avatar"] = avatar

    return AgentVisualResult(provider=provider, prompt=prompt, files=files)


def _resolve_agnes_config() -> dict[str, str]:
    env_key = (os.getenv("AGNES_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    env_base_url = os.getenv("AGNES_BASE_URL", "").strip() or "https://apihub.agnes-ai.com/v1"
    env_model = os.getenv("AGNES_IMAGE_MODEL", "").strip()
    config = {
        "api_key": env_key,
        "base_url": env_base_url,
        "model": env_model or "agnes-image-2.1-flash",
    }
    if config["api_key"]:
        return config

    entry = _load_agnes_custom_model_entry()
    if not entry:
        return config

    api_key = str(entry.get("api_key") or "").strip()
    base_url = str(entry.get("base_url") or "").strip()
    model = _pick_agnes_image_model(entry) or config["model"]
    return {
        "api_key": api_key,
        "base_url": base_url or config["base_url"],
        "model": env_model or model,
    }


def _load_agnes_custom_model_entry() -> dict[str, Any] | None:
    try:
        from runtime.platform.process.paths import app_paths

        path = app_paths().custom_models_path
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    candidates = [
        entry
        for entry in data.values()
        if isinstance(entry, dict) and _is_agnes_custom_model_entry(entry)
    ]
    return candidates[0] if candidates else None


def _is_agnes_custom_model_entry(entry: dict[str, Any]) -> bool:
    base_url = str(entry.get("base_url") or "").lower()
    if "agnes-ai.com" in base_url:
        return True
    models = entry.get("models")
    if isinstance(models, list):
        return any(str(model).startswith("agnes-") for model in models)
    return str(entry.get("model") or "").startswith("agnes-")


def _pick_agnes_image_model(entry: dict[str, Any]) -> str | None:
    models = entry.get("models")
    if isinstance(models, list):
        for model in models:
            model_name = str(model or "").strip()
            if model_name.startswith("agnes-image-"):
                return model_name
    model = str(entry.get("model") or "").strip()
    return model if model.startswith("agnes-image-") else None


def _agent_visual_view_prompt(
    *,
    base_prompt: str,
    view: str,
    agent_id: str,
    display_name: str,
) -> str:
    view_label = {
        "front": "front view, facing the viewer",
        "side": "side profile view, facing screen right",
        "back": "back view, showing rear silhouette and equipment",
    }.get(view, view)
    return (
        f"{base_prompt} Generate ONLY the {view_label}. "
        f"Follow {AGENT_VISUAL_METASKILL_ID} view-specific constraints. "
        "Use a single full-body character centered in the frame with generous "
        "transparent padding above the head, around the hair, and below the feet. "
        f"Maintain the same identity, outfit language, palette, and art style as "
        f"the other views of '{display_name}'. "
        "Transparent background is preferred; if unavailable, use one perfectly "
        "flat #00ff00 chroma-key background with no floor, no shadow, no gradient, "
        "no environment, and no props outside the character. "
        "Do not include any text, UI labels, skill panels, name cards, borders, "
        "decorative HUD, diagram lines, infographic elements, floating icons, "
        "floating particles, or detached code glyphs."
    )


def _agent_visual_avatar_prompt(
    *,
    base_prompt: str,
    agent_id: str,
    display_name: str,
) -> str:
    return (
        f"{base_prompt} Generate ONLY a square close-up avatar portrait for "
        f"'{display_name}' ({agent_id}). Follow {AGENT_VISUAL_METASKILL_ID} "
        "avatar constraints. Use the same face, hairstyle, outfit collar, "
        "palette, temperament, and art style as the front/side/back views. "
        "Composition: large head and face fill most of the frame, front-facing "
        "or slight three-quarter front view, eyes sharp and centered, include "
        "hair and only slight shoulder/collar context, no full body, no half "
        "body. Output a clean square 1:1 avatar suitable for small list icons. "
        "Transparent background is preferred; if unavailable, use one perfectly "
        "flat #00ff00 chroma-key background. Do not include any text, labels, "
        "UI frame, border, watermark, badge, poster, infographic, floating "
        "icons, particles, or detached decorative elements."
    )


def _post_agnes_image_generation(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    size: str,
    timeout: int,
    reference_images: list[str] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": 1,
    }
    clean_reference_images = [
        image.strip() for image in (reference_images or []) if image and image.strip()
    ][:3]
    if clean_reference_images:
        body["extra_body"] = {
            "image": clean_reference_images[0]
            if len(clean_reference_images) == 1
            else clean_reference_images
        }
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/images/generations",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 — audited HTTPS image API
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"agnes API error: HTTP {exc.code} - {body[:800]}") from exc
    except OSError as exc:
        raise RuntimeError(f"agnes API request failed: {type(exc).__name__}: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"agnes API returned non-JSON: {raw[:200]!r}") from exc


def _write_agnes_image_result(data: dict[str, Any], output: Path, *, timeout: int) -> None:
    items = data.get("data")
    if not isinstance(items, list) or not items:
        raise RuntimeError(f"agnes API returned no image data: {data!r}")
    first = items[0]
    if not isinstance(first, dict):
        raise RuntimeError(f"agnes API returned invalid image data: {first!r}")

    if isinstance(first.get("b64_json"), str) and first["b64_json"]:
        output.write_bytes(base64.b64decode(first["b64_json"]))
        return

    url = first.get("url")
    if not isinstance(url, str) or not url:
        raise RuntimeError(f"agnes API returned no image URL: {first!r}")
    req = urllib.request.Request(url, headers={"Accept": "image/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 — audited HTTPS image download
            output.write_bytes(resp.read())
    except OSError as exc:
        raise RuntimeError(f"agnes image download failed: {type(exc).__name__}: {exc}") from exc


def _postprocess_agent_visual(path: Path) -> None:
    """Remove flat generated backgrounds, crop to subject, and add safe padding."""
    try:
        from PIL import Image  # noqa: F401 — availability check
    except ImportError:
        return

    try:
        image = Image.open(path).convert("RGBA")
    except OSError:
        return

    alpha = image.getchannel("A")
    has_existing_alpha = alpha.getextrema()[0] < 250
    matte = alpha if has_existing_alpha else _connected_flat_background_alpha(image)

    image.putalpha(matte)
    image = _keep_primary_alpha_component(image)
    matte = image.getchannel("A")
    bbox = matte.getbbox()
    if not bbox:
        image.save(path)
        return

    width, height = image.size
    x0, y0, x1, y1 = bbox
    pad_x = max(48, int((x1 - x0) * 0.14))
    pad_top = max(96, int((y1 - y0) * 0.14))
    pad_bottom = max(72, int((y1 - y0) * 0.08))
    crop_box = (
        max(0, x0 - pad_x),
        max(0, y0 - pad_top),
        min(width, x1 + pad_x),
        min(height, y1 + pad_bottom),
    )
    image = image.crop(crop_box)
    # Add a little extra transparent safety space even if the crop hit an edge.
    safety = Image.new(
        "RGBA",
        (
            image.width + max(32, image.width // 16) * 2,
            image.height + max(48, image.height // 12) + max(32, image.height // 20),
        ),
        (0, 0, 0, 0),
    )
    safety.alpha_composite(image, (max(32, image.width // 16), max(48, image.height // 12)))
    safety.save(path)


def _postprocess_avatar_image(path: Path) -> None:
    """Normalize a generated avatar to a clean transparent 512x512 headshot."""
    try:
        from PIL import Image
    except ImportError:
        return

    try:
        image = Image.open(path).convert("RGBA")
    except OSError:
        return

    alpha = image.getchannel("A")
    has_existing_alpha = alpha.getextrema()[0] < 250
    image.putalpha(alpha if has_existing_alpha else _connected_flat_background_alpha(image))
    image = _keep_primary_alpha_component(image)

    bbox = image.getchannel("A").getbbox()
    if bbox:
        x0, y0, x1, y1 = bbox
        width = x1 - x0
        height = y1 - y0
        pad_x = max(8, int(width * 0.04))
        pad_top = max(8, int(height * 0.04))
        pad_bottom = max(8, int(height * 0.06))
        image = image.crop(
            (
                max(0, x0 - pad_x),
                max(0, y0 - pad_top),
                min(image.width, x1 + pad_x),
                min(image.height, y1 + pad_bottom),
            )
        )

    canvas_size = max(image.width, image.height)
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    canvas.alpha_composite(
        image,
        ((canvas_size - image.width) // 2, (canvas_size - image.height) // 2),
    )
    avatar = canvas.resize((512, 512), Image.Resampling.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    avatar.save(path)


def _keep_primary_alpha_component(image: Any) -> Any:
    try:
        from PIL import Image
    except ImportError:
        return image

    alpha = image.getchannel("A")
    width, height = alpha.size
    px = alpha.load()
    visited = bytearray(width * height)
    components: list[tuple[int, int, int, int, int, int, int]] = []

    for start_y in range(height):
        for start_x in range(width):
            start_idx = start_y * width + start_x
            if visited[start_idx] or px[start_x, start_y] <= 24:
                continue
            queue: deque[tuple[int, int]] = deque([(start_x, start_y)])
            visited[start_idx] = 1
            count = 0
            x0 = x1 = start_x
            y0 = y1 = start_y
            while queue:
                x, y = queue.popleft()
                count += 1
                x0 = min(x0, x)
                x1 = max(x1, x)
                y0 = min(y0, y)
                y1 = max(y1, y)
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    idx = ny * width + nx
                    if visited[idx] or px[nx, ny] <= 24:
                        continue
                    visited[idx] = 1
                    queue.append((nx, ny))
            components.append((count, x0, y0, x1, y1, start_x, start_y))

    if not components:
        return image

    # Keep the largest component plus near-touching antialiasing. This removes
    # floating prompt artifacts while preserving the actual character silhouette.
    count, _x0, _y0, _x1, _y1, seed_x, seed_y = max(
        components,
        key=lambda item: item[0],
    )
    if count < width * height * 0.01:
        return image

    keep = Image.new("L", (width, height), 0)
    keep_px = keep.load()
    alpha_px = alpha.load()
    visited = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque([(seed_x, seed_y)])
    visited[seed_y * width + seed_x] = 1
    while queue:
        x, y = queue.popleft()
        keep_px[x, y] = alpha_px[x, y]
        # The high-alpha scan above finds the authoritative subject. Expand
        # from that seed across every original non-transparent edge pixel so
        # antialiasing is preserved exactly, including diagonal hair and strap
        # details. Detached prompt artifacts have no alpha-connected path and
        # are therefore removed without dilating or redrawing the silhouette.
        for nx, ny in (
            (x - 1, y - 1),
            (x, y - 1),
            (x + 1, y - 1),
            (x - 1, y),
            (x + 1, y),
            (x - 1, y + 1),
            (x, y + 1),
            (x + 1, y + 1),
        ):
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            idx = ny * width + nx
            if visited[idx] or alpha_px[nx, ny] == 0:
                continue
            visited[idx] = 1
            queue.append((nx, ny))
    out = image.copy()
    out.putalpha(keep)
    return out


def _connected_flat_background_alpha(image: Any) -> Any:
    from PIL import Image, ImageFilter

    rgb = image.convert("RGB")
    width, height = rgb.size
    px = rgb.load()
    samples: list[tuple[int, int, int]] = []
    sample_points = [
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
        (width // 2, 0),
        (width // 2, height - 1),
        (0, height // 2),
        (width - 1, height // 2),
    ]
    for point in sample_points:
        samples.append(px[point])

    def is_background(x: int, y: int) -> bool:
        r, g, b = px[x, y]
        # Strong chroma key requested in the prompt.
        if g > 150 and r < 110 and b < 130:
            return True
        return any(abs(r - sr) + abs(g - sg) + abs(b - sb) <= 74 for sr, sg, sb in samples)

    visited = bytearray(width * height)
    background = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def add(x: int, y: int) -> None:
        if x < 0 or y < 0 or x >= width or y >= height:
            return
        idx = y * width + x
        if visited[idx] or not is_background(x, y):
            return
        visited[idx] = 1
        background[idx] = 1
        queue.append((x, y))

    for x in range(width):
        add(x, 0)
        add(x, height - 1)
    for y in range(height):
        add(0, y)
        add(width - 1, y)

    while queue:
        x, y = queue.popleft()
        add(x - 1, y)
        add(x + 1, y)
        add(x, y - 1)
        add(x, y + 1)

    matte = Image.new("L", (width, height), 255)
    matte_px = matte.load()
    for y in range(height):
        row = y * width
        for x in range(width):
            if background[row + x]:
                matte_px[x, y] = 0
    return matte.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.45))


def _make_avatar_from_front(front_path: Path, avatar_path: Path) -> Path | None:
    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        image = Image.open(front_path).convert("RGBA")
    except OSError:
        return None

    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return None

    x0, y0, x1, y1 = bbox
    subject_w = x1 - x0
    subject_h = y1 - y0
    cx = (x0 + x1) // 2
    # List avatars need a close-up head read. Use the top of the extracted
    # standee as the head anchor and keep only a little shoulder context.
    top = max(0, y0 - int(subject_h * 0.03))
    bottom = min(image.height, y0 + int(subject_h * 0.22))
    face_window_h = bottom - top
    side = max(int(subject_w * 0.46), int(face_window_h * 0.92))
    left = max(0, cx - side // 2)
    right = min(image.width, left + side)
    left = max(0, right - side)
    crop = image.crop((left, top, right, bottom))
    canvas_size = max(crop.width, crop.height)
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    canvas.alpha_composite(
        crop, ((canvas_size - crop.width) // 2, (canvas_size - crop.height) // 2)
    )
    avatar = canvas.resize((512, 512), Image.Resampling.LANCZOS)
    avatar_path.parent.mkdir(parents=True, exist_ok=True)
    avatar.save(avatar_path)
    return avatar_path


def _generate_with_command(
    *,
    provider: str,
    prompt: str,
    agent_id: str,
    display_name: str,
    output_dir: Path,
) -> AgentVisualResult:
    command_template = os.getenv("ECHO_IMAGE_GEN_COMMAND")
    if not command_template and provider == "opencli-jimeng":
        command_template = 'opencli jimeng generate --prompt "$prompt" --output "$output"'
    if not command_template:
        raise RuntimeError("ECHO_IMAGE_GEN_COMMAND is required for this image provider")

    output = output_dir / "reference.png"
    # 对用户可控的文本字段进行 shell 转义,防止命令注入。
    # prompt / agent_id / display_name 可能包含 shell 元字符(如 ; ` $ 等),
    # 必须用 shlex.quote 包裹后再代入模板,使模板中的 "$prompt" 等占位符
    # 在 shell 中被解释为字面字符串。output/output_dir 是受控路径,同样转义以防边界情况。
    variables = {
        "agent_id": shlex.quote(agent_id),
        "display_name": shlex.quote(display_name),
        "prompt": shlex.quote(prompt),
        "output": shlex.quote(str(output)),
        "output_dir": shlex.quote(str(output_dir)),
    }
    command = Template(command_template).safe_substitute(variables)
    timeout = int(os.getenv("ECHO_IMAGE_GEN_TIMEOUT_SECONDS") or "180")
    completed = subprocess.run(  # nosec B602 — operator-configured template; every interpolated variable is shlex-quoted above
        command,
        cwd=str(output_dir),
        shell=True,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            f"image generation command failed ({completed.returncode}): {stderr[:800]}"
        )
    if not output.is_file():
        raise RuntimeError(
            "image generation command completed but did not create the expected output"
        )

    files = {view: output for view in AGENT_VISUAL_VIEWS}
    return AgentVisualResult(provider=provider, prompt=prompt, files=files)


def _generate_mock_visuals(
    *,
    provider: str,
    prompt: str,
    agent_id: str,
    display_name: str,
    output_dir: Path,
) -> AgentVisualResult:
    files: dict[str, Path] = {}
    for view in AGENT_VISUAL_VIEWS:
        out = output_dir / f"{view}.svg"
        out.write_text(
            _mock_visual_svg(agent_id=agent_id, display_name=display_name, view=view),
            encoding="utf-8",
        )
        files[view] = out
    avatar = output_dir.parent / "avatar.svg"
    avatar.write_text(
        _mock_avatar_svg(agent_id=agent_id, display_name=display_name),
        encoding="utf-8",
    )
    files["avatar"] = avatar
    return AgentVisualResult(provider=provider, prompt=prompt, files=files)


def _mock_avatar_svg(*, agent_id: str, display_name: str) -> str:
    seed = sum(ord(c) for c in agent_id) % 360
    hue_a = (seed + 42) % 360
    hue_b = (seed + 290) % 360
    safe_name = _escape_xml((display_name[:2] or agent_id[:2] or "AG").upper())
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="96" fill="hsl({hue_b}, 72%, 18%)"/>
  <circle cx="256" cy="202" r="126" fill="hsl({hue_a}, 92%, 70%)"/>
  <path d="M94 520c12-118 78-182 162-182s150 64 162 182" fill="hsl({hue_b}, 82%, 56%)"/>
  <circle cx="212" cy="190" r="17" fill="#171b23"/>
  <circle cx="300" cy="190" r="17" fill="#171b23"/>
  <path d="M214 254c28 28 56 28 84 0" fill="none" stroke="#171b23" stroke-width="18" stroke-linecap="round"/>
  <text x="256" y="454" text-anchor="middle" font-family="Arial, sans-serif" font-size="54" font-weight="800" fill="#ffffff">{safe_name}</text>
</svg>"""


def _mock_visual_svg(*, agent_id: str, display_name: str, view: str) -> str:
    width = {"front": 210, "side": 118, "back": 220}.get(view, 210)
    opacity = "0.72" if view == "back" else "1"
    seed = sum(ord(c) for c in agent_id) % 360
    hue_a = (seed + 42) % 360
    hue_b = (seed + 290) % 360
    safe_name = _escape_xml(display_name[:30] or agent_id)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="820" viewBox="0 0 640 820">
  <defs>
    <linearGradient id="body" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="hsl({hue_a}, 92%, 70%)"/>
      <stop offset="0.58" stop-color="hsl({hue_b}, 82%, 58%)"/>
      <stop offset="1" stop-color="#171b23"/>
    </linearGradient>
    <radialGradient id="core" cx="50%" cy="28%" r="58%">
      <stop offset="0" stop-color="#ffe28a"/>
      <stop offset="0.46" stop-color="#ff9f6e"/>
      <stop offset="1" stop-color="#df3cf0"/>
    </radialGradient>
    <filter id="glow"><feGaussianBlur stdDeviation="9" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  </defs>
  <ellipse cx="320" cy="704" rx="248" ry="52" fill="#facc15" opacity=".10" stroke="#facc15" stroke-width="2"/>
  <ellipse cx="320" cy="710" rx="158" ry="32" fill="#000000" opacity=".20" stroke="#ffffff" stroke-width="1"/>
  <g opacity="{opacity}" filter="url(#glow)">
    <rect x="{320 - width / 2:.1f}" y="220" width="{width}" height="330" rx="{min(width / 2, 92):.1f}" fill="url(#body)" opacity=".22" stroke="#7dd3fc" stroke-width="2"/>
    <circle cx="320" cy="260" r="{58 if view != "side" else 45}" fill="url(#core)"/>
    <rect x="{320 - width * 0.34:.1f}" y="320" width="{width * 0.68:.1f}" height="202" rx="{width * 0.22:.1f}" fill="url(#body)" stroke="#facc15" stroke-width="2" opacity=".96"/>
    <path d="M{320 - width * 0.42:.1f} 396 C{320 - width * 0.75:.1f} 448 {320 - width * 0.72:.1f} 540 {320 - width * 0.36:.1f} 584" fill="none" stroke="#f472b6" stroke-width="34" stroke-linecap="round"/>
    <path d="M{320 + width * 0.42:.1f} 396 C{320 + width * 0.75:.1f} 448 {320 + width * 0.72:.1f} 540 {320 + width * 0.36:.1f} 584" fill="none" stroke="#f472b6" stroke-width="34" stroke-linecap="round"/>
    <path d="M{320 - width * 0.2:.1f} 514 C{320 - width * 0.42:.1f} 586 {320 - width * 0.3:.1f} 650 {320 - width * 0.05:.1f} 670" fill="none" stroke="#df3cf0" stroke-width="32" stroke-linecap="round"/>
    <path d="M{320 + width * 0.2:.1f} 514 C{320 + width * 0.42:.1f} 586 {320 + width * 0.3:.1f} 650 {320 + width * 0.05:.1f} 670" fill="none" stroke="#df3cf0" stroke-width="32" stroke-linecap="round"/>
    <circle cx="{320 - width * 0.42:.1f}" cy="246" r="14" fill="#37306b"/>
    <circle cx="{320 + width * 0.42:.1f}" cy="246" r="14" fill="#37306b"/>
  </g>
  <text x="320" y="764" text-anchor="middle" font-family="Arial, sans-serif" font-size="22" font-weight="700" fill="#ffffff">{safe_name}</text>
</svg>"""


def _escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
