"""Image semantic-search skills (local image library).

Wraps :mod:`runtime.memory.hemolymph.image_semantic_index` as agent tools:
  * ``image_search_by_text``  — text description → matching images (CLIP)
  * ``image_search_by_image`` — image file → visually similar images (CLIP)
  * ``face_group_albums``     — cluster indexed faces into person groups
  * ``face_search_by_image``  — image file → images containing the same face

All four are self-gating: when the CLIP tower / face model isn't available or
``ECHO_IMAGE_SEMANTIC=0``, they return a clear message instead of failing —
so the agent degrades to a plain filesystem listing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .registry import Skill

if TYPE_CHECKING:
    from .registry import SkillRegistry

from runtime.memory.hemolymph import image_semantic_index as _idx


def _image_search_by_text(
    query: str = "",
    *,
    directory: str = ".",
    top_k: int = 10,
    **_kw: Any,
) -> dict[str, Any]:
    if not query.strip():
        return {"error": "missing query"}
    results = _idx.search_by_text(query, top_k=top_k, db_path=_idx_db(directory))
    if results is None:
        return _not_ready("semantic image search")
    return {"query": query, "backend": "clip-text", "count": len(results), "results": results}


def _image_search_by_image(
    image_path: str = "",
    *,
    directory: str = ".",
    top_k: int = 10,
    **_kw: Any,
) -> dict[str, Any]:
    if not image_path:
        return {"error": "missing image_path"}
    results = _idx.search_by_image(image_path, top_k=top_k, db_path=_idx_db(directory))
    if results is None:
        return _not_ready("image-to-image search")
    return {
        "query_image": image_path,
        "backend": "clip-vision",
        "count": len(results),
        "results": results,
    }


def _face_group_albums(
    *,
    directory: str = ".",
    threshold: float = 0.45,
    **_kw: Any,
) -> dict[str, Any]:
    groups = _idx.group_faces(db_path=_idx_db(directory), threshold=threshold)
    if groups is None:
        return _not_ready("face grouping", face=True)
    return {"backend": "arcface", "person_count": len(groups), "groups": groups}


def _face_search_by_image(
    image_path: str = "",
    *,
    directory: str = ".",
    top_k: int = 10,
    **_kw: Any,
) -> dict[str, Any]:
    if not image_path:
        return {"error": "missing image_path"}
    results = _idx.search_face(image_path, top_k=top_k, db_path=_idx_db(directory))
    if results is None:
        return _not_ready("face search", face=True)
    return {
        "query_image": image_path,
        "backend": "arcface",
        "count": len(results),
        "results": results,
    }


def _idx_db(directory: str) -> str:
    """Point the index at a directory-scoped DB so different libraries don't
    collide. Falls back to the default ``data/image_index.db`` on empty dir."""
    import os
    from pathlib import Path

    d = (directory or ".").strip()
    if not d or d == ".":
        return "data/image_index.db"
    safe = os.path.basename(os.path.normpath(d)) or "image_lib"
    return str(Path("data") / f"image_index_{safe}.db")


def _not_ready(feature: str, *, face: bool = False) -> dict[str, Any]:
    if face:
        return {
            "error": "face_analysis_unavailable",
            "hint": "请先执行 image_index_build 建立图片索引（含人脸），或确认 insightface 可用。",
            "results": [],
        }
    return {
        "error": "image_semantic_unavailable",
        "hint": "请先执行 image_index_build 建立图片索引，或确认 CLIP 模型可用。",
        "results": [],
    }


def _image_index_build(
    *,
    directory: str = ".",
    include_faces: bool = True,
    **_kw: Any,
) -> dict[str, Any]:
    """Explicit (re)build of the image index for a directory."""
    summary = _idx.build_index(directory, db_path=_idx_db(directory), include_faces=include_faces)
    if summary.get("ok") is False:
        return {
            "error": summary.get("error", "index_build_failed"),
            "hint": "请确认目录存在且包含图片文件，或 CLIP 模型可用。",
        }
    return summary


def register_image_semantic_skills(registry: SkillRegistry) -> int:
    registry.register(
        Skill(
            name="image_index_build",
            description=(
                "建立/重建本地图片语义索引。扫描目录下的图片，用 CLIP 生成向量，"
                "可选（默认开启）用洞察人脸模型提取人脸向量。Args: "
                "{directory?: string, include_faces?: boolean}。首次语义检索前必须先调用本工具。"
            ),
            affinity=["image", "vision", "index"],
            cost_profile="high",
            trusted_source="skill://private/image_index_build",
            handler=_image_index_build,
        )
    )
    registry.register(
        Skill(
            name="image_search_by_text",
            description=(
                "用文字描述在本地图片库中检索语义最相近的图片（CLIP 文→图）。"
                "Args: {query: string, directory?: string, top_k?: int}。"
                "需先执行 image_index_build 建立索引。"
            ),
            affinity=["image", "vision", "search", "rag"],
            cost_profile="mid",
            trusted_source="skill://private/image_search_by_text",
            handler=_image_search_by_text,
        )
    )
    registry.register(
        Skill(
            name="image_search_by_image",
            description=(
                "用一张图片在本地图片库中检索视觉最相似的图片（CLIP 图→图，以图搜图）。"
                "Args: {image_path: string, directory?: string, top_k?: int}。"
                "需先执行 image_index_build 建立索引。"
            ),
            affinity=["image", "vision", "search"],
            cost_profile="mid",
            trusted_source="skill://private/image_search_by_image",
            handler=_image_search_by_image,
        )
    )
    registry.register(
        Skill(
            name="face_group_albums",
            description=(
                "把索引图片中的人脸按身份聚类成人物分组（AI 相册的人脸分组）。"
                "Args: {directory?: string, threshold?: number}。"
                "需先执行 image_index_build（含人脸）建立索引。"
            ),
            affinity=["image", "face", "album"],
            cost_profile="mid",
            trusted_source="skill://private/face_group_albums",
            handler=_face_group_albums,
        )
    )
    registry.register(
        Skill(
            name="face_search_by_image",
            description=(
                "用一张图片在本地图片库中检索包含同一人的图片（人脸以图搜人）。"
                "Args: {image_path: string, directory?: string, top_k?: int}。"
                "需先执行 image_index_build（含人脸）建立索引。"
            ),
            affinity=["image", "face", "search"],
            cost_profile="mid",
            trusted_source="skill://private/face_search_by_image",
            handler=_face_search_by_image,
        )
    )
    return 5


__all__ = ["register_image_semantic_skills"]
