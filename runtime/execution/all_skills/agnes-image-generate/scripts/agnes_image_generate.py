"""AI image generation skill — dual-provider (Volcano / Agnes).

Wraps ``POST {base_url}/images/generations``. The provider is auto-detected
from ``base_url``:

- **Volcano (火山方舟)** — ``https://ark.cn-beijing.volces.com/api/plan/v3``,
  model ``doubao-seedream-5.0-lite``（文生图 + 图生图）。
- **Agnes AI Gateway** — ``https://apihub.agnes-ai.com/v1``,
  model ``agnes-image-2.1-flash``（OpenAI 兼容）。

Both are thin adapters: Authorization header + body shape, returns hosted
image URL(s).

Usage:
    from agnes_image_generate import generate_image
    r = generate_image("a cat astronaut on Mars")
    print(r["url"])

API key resolution (first match wins):
    Volcano: ``VOLCENGINE_API_KEY`` / ``ARK_API_KEY``
    Agnes:   ``AGNES_API_KEY``
    Fallback: ``OPENAI_API_KEY``
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

_LOG = logging.getLogger(__name__)

# Volcano (火山方舟) — Agent Plan 套餐端点
VOLC_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
VOLC_MODEL = "doubao-seedream-5.0-lite"

# Agnes AI Gateway — OpenAI 兼容
AGNES_URL = "https://apihub.agnes-ai.com/v1"
AGNES_MODEL = "agnes-image-2.1-flash"

DEFAULT_BASE_URL = VOLC_URL
DEFAULT_MODEL = VOLC_MODEL
TIMEOUT_SECONDS = 300


def _is_volcano(base_url: str) -> bool:
    """True when the base URL points at Volcano Ark (vs. Agnes)."""
    return "volces.com" in (base_url or "").lower()


def _resolve_api_key() -> str:
    """Pick the first available API key across the supported providers."""
    for var in ("VOLCENGINE_API_KEY", "ARK_API_KEY", "AGNES_API_KEY", "OPENAI_API_KEY"):
        val = os.environ.get(var, "").strip()
        if val:
            return val
    return ""


@dataclass(frozen=True)
class AgnesConfig:
    """Runtime config resolved from env vars."""

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    timeout: int = TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> AgnesConfig:
        key = _resolve_api_key()
        if not key:
            raise ValueError(
                "No API key found. Set VOLCENGINE_API_KEY / ARK_API_KEY "
                "(Volcano) or AGNES_API_KEY / OPENAI_API_KEY (Agnes), "
                "or pass api_key= explicitly.",
            )
        base = (
            os.environ.get("VOLCENGINE_BASE_URL", "").strip()
            or os.environ.get("AGNES_BASE_URL", "").strip()
            or DEFAULT_BASE_URL
        ).rstrip("/")
        return cls(api_key=key, base_url=base)


def generate_image(
    prompt: str,
    *,
    model: str | None = None,
    size: str | None = None,
    n: int = 1,
    image: str | list[str] | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate one or more images via Volcano or Agnes.

    Parameters
    ----------
    prompt
        Text description of the desired image. Required.
    model
        Model id. Defaults to ``doubao-seedream-5.0-lite`` (Volcano) when the
        base URL is Volcano, else ``agnes-image-2.1-flash``.
    size
        Optional WxH string like ``"2048x2048"`` (Volcano) or ``"1024x1024"``
        (Agnes). When omitted the gateway picks a sensible default.
    n
        Number of images to generate.
    image
        Optional reference image URL (or list of URLs) for image→image.
    api_key, base_url
        Override env-resolved config. ``base_url`` should NOT include the
        ``/images/generations`` suffix — it's appended automatically.
    extra
        Extra fields merged into the request body for forward compatibility.

    Returns
    -------
    dict
        ``{"url": str, "urls": list[str], "model": str, "created": int,
        "usage": dict}``. ``url`` is the first URL when ``n > 1``.

    Raises
    ------
    ValueError
        If ``prompt`` is empty or no API key resolved.
    RuntimeError
        If the gateway returns a non-200 status.
    """
    if not prompt or not str(prompt).strip():
        raise ValueError("prompt is required")
    if api_key is None or base_url is None:
        cfg = AgnesConfig.from_env()
        api_key = api_key or cfg.api_key
        base_url = (base_url or cfg.base_url).rstrip("/")

    volcano = _is_volcano(base_url)
    if model is None:
        model = VOLC_MODEL if volcano else AGNES_MODEL

    payload: dict[str, Any] = {
        "model": model,
        "prompt": str(prompt).strip(),
        "n": max(1, int(n)),
    }
    if size:
        payload["size"] = size
    if image is not None:
        # Volcano: image is a first-class request field; Agnes keeps it
        # under extra_body for backward compatibility.
        if volcano:
            payload["image"] = image
        else:
            payload.setdefault("extra_body", {})["image"] = image
    if extra:
        for key, value in extra.items():
            if key == "extra_body" and isinstance(value, dict):
                payload.setdefault("extra_body", {}).update(value)
            elif key not in payload:
                payload[key] = value

    url = f"{base_url}/images/generations"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    _LOG.info(
        "image_generate provider=%s model=%s n=%d size=%s",
        "volcano" if volcano else "agnes",
        model,
        payload["n"],
        size or "auto",
    )

    try:
        resp = requests.post(
            url,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RuntimeError(
            f"image API request failed: {type(exc).__name__}: {exc}",
        ) from exc

    if resp.status_code != 200:
        body = resp.text[:500].replace("\n", " ")
        raise RuntimeError(
            f"image API error: HTTP {resp.status_code} — {body}",
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(
            f"image API returned non-JSON: {resp.text[:200]!r}",
        ) from exc

    items = data.get("data") or []
    urls: list[str] = []
    for item in items:
        if isinstance(item, dict):
            u = item.get("url") or item.get("image_url")
            if u:
                urls.append(str(u))

    return {
        "url": urls[0] if urls else "",
        "urls": urls,
        "model": data.get("model") or model,
        "created": data.get("created"),
        "usage": data.get("usage") or {},
        "raw": data,
    }


__all__ = ["AgnesConfig", "generate_image"]


if __name__ == "__main__":  # pragma: no cover — manual smoke test
    import argparse

    parser = argparse.ArgumentParser(description="Image generate (Volcano/Agnes)")
    parser.add_argument("prompt", help="Text prompt")
    parser.add_argument("--model", default=None)
    parser.add_argument("--size", default=None)
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override base URL (e.g. Volcano plan/v3 or Agnes /v1)",
    )
    args = parser.parse_args()

    result = generate_image(
        args.prompt,
        model=args.model,
        size=args.size,
        n=args.n,
        base_url=args.base_url,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
