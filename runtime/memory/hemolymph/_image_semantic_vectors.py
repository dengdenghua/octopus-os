"""Pure image/vector helpers for the semantic image index.

Extracted from :mod:`image_semantic_index` so that module stays under the
god-file line budget. These functions have no state and no DB access —
they transform pixels, blobs, or vectors. Self-gated: every one returns a
safe fallback (``""`` / ``0.0`` / ``1 << 30``) instead of raising when the
optional imaging stack (PIL / OpenCV) is unavailable or input is malformed.
"""

from __future__ import annotations

import array
import math
from typing import Any

from PIL import Image


def _compute_dhash(img, size: int = 8) -> str:
    """Compute difference hash (dHash) for image similarity / duplicate detection.
    8x8 → 64 bits → 16 hex chars."""
    try:
        gray = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
        diff = ""
        for row in range(size):
            for col in range(size):
                left = gray.getpixel((col, row))
                right = gray.getpixel((col + 1, row))
                diff += "1" if right > left else "0"
        return hex(int(diff, 2))
    except Exception:  # noqa: BLE001
        return ""


def _laplacian_sharpness(img) -> float:
    """Compute Laplacian variance for blur detection. Lower variance → more blurry.

    Self-gated: when OpenCV is unavailable the import is INSIDE the try so the
    function degrades to 0.0 instead of raising into the index builder.
    """
    try:
        import cv2
        import numpy as np

        gray = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
        lap = cv2.Laplacian(gray, cv2.CV_64F)
        return lap.var()
    except Exception:  # noqa: BLE001
        return 0.0


def _cosine(a, b) -> float:
    if a is None or b is None or len(a) == 0 or len(b) == 0 or len(a) != len(b):
        return 0.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(float(x) * float(x) for x in a)) or 1e-9
    nb = math.sqrt(sum(float(x) * float(x) for x in b)) or 1e-9
    return dot / (na * nb)


def _blob_to_vec(blob: bytes) -> list[float]:
    a = array.array("f")
    a.frombytes(bytes(blob))
    return list(a)


def _vec_to_blob(vec: Any) -> bytes:
    return array.array("f", (float(x) for x in vec)).tobytes()


def _ham_dist(a: str, b: str) -> int:
    """Hamming distance between two dHash hex strings (bitwise)."""
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 1 << 30


__all__ = [
    "_blob_to_vec",
    "_compute_dhash",
    "_cosine",
    "_ham_dist",
    "_laplacian_sharpness",
    "_vec_to_blob",
]
