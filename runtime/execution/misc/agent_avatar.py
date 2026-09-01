"""Shared SVG avatar assets for local agents."""

from __future__ import annotations

from html import escape
from pathlib import Path


def pixel_agent_avatar_svg(label: str = "Agent") -> str:
    """Return a monochrome half-body pixel portrait SVG."""
    safe_label = escape(label or "Agent", quote=False)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96" shape-rendering="crispEdges">
  <title>{safe_label} pixel avatar</title>
  <rect width="96" height="96" fill="#fff"/>
  <path fill="#111" d="M30 12h36v4h8v6h4v18h-4v14h-4V32H26v22h-4V40h-4V22h4v-6h8z"/>
  <path fill="#fff" d="M28 32h40v8h4v18h-4v10h-6v6H34v-6h-6V58h-4V40h4z"/>
  <path fill="#111" d="M20 40h6v18h-6zm50 0h6v18h-6zM32 44h12v4H32zm20 0h12v4H52zm-8 8h8v4h-8zm-6 12h20v4H38zm4 4h12v4H42z"/>
  <path fill="#111" d="M34 72h28v4h8v4h8v12H18V80h8v-4h8z"/>
  <path fill="#fff" d="M36 76h24v4h4v4h4v4H28v-4h4v-4h4z"/>
</svg>
"""


def write_pixel_agent_avatar(path: Path, label: str = "Agent") -> None:
    path.write_text(pixel_agent_avatar_svg(label), encoding="utf-8")


__all__ = ["pixel_agent_avatar_svg", "write_pixel_agent_avatar"]
