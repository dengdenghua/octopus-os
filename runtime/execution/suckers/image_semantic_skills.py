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

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .registry import Skill

if TYPE_CHECKING:
    from .registry import SkillRegistry

from echo_runtime.resource_identity import photo_asset_reference, photo_library_id, photo_source
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
    return {
        "query": query,
        "backend": "clip-text",
        "count": len(results),
        "results": _decorate_results(results, directory),
        "source": _library_source(directory),
    }


def _image_search_by_image(
    image_path: str = "",
    *,
    directory: str = ".",
    top_k: int = 10,
    **_kw: Any,
) -> dict[str, Any]:
    if not image_path:
        return {"error": "missing image_path"}
    results = _idx.search_by_image(
        _source_path(directory, image_path), top_k=top_k, db_path=_idx_db(directory)
    )
    if results is None:
        return _not_ready("image-to-image search")
    return {
        "query_image": image_path,
        "backend": "clip-vision",
        "count": len(results),
        "results": _decorate_results(results, directory),
        "queryAssetReference": _asset_reference(directory, image_path),
        "source": _library_source(directory),
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
    return {
        "backend": "arcface",
        "person_count": len(groups),
        "groups": groups,
        "source": _library_source(directory),
    }


def _face_search_by_image(
    image_path: str = "",
    *,
    directory: str = ".",
    top_k: int = 10,
    **_kw: Any,
) -> dict[str, Any]:
    if not image_path:
        return {"error": "missing image_path"}
    results = _idx.search_face(
        _source_path(directory, image_path), top_k=top_k, db_path=_idx_db(directory)
    )
    if results is None:
        return _not_ready("face search", face=True)
    return {
        "query_image": image_path,
        "backend": "arcface",
        "count": len(results),
        "results": _decorate_results(results, directory),
        "queryAssetReference": _asset_reference(directory, image_path),
        "source": _library_source(directory),
    }


def _idx_db(directory: str) -> str:
    """Bind every library (including cwd) to its canonical full path.

    Old basename-only databases have no trustworthy source identity, so they
    must not be adopted automatically. Rebuilding creates an isolated index
    and leaves those legacy files untouched.
    """
    from runtime.platform.process.paths import app_paths

    library_id = photo_library_id(directory)
    return str(app_paths().data_dir / "image_libraries" / library_id / "index.db")


def _library_source(directory: str) -> dict[str, Any]:
    """Return a path-free source envelope for Agent image results."""

    source = photo_source(photo_library_id(directory))
    try:
        info = Path(_idx_db(directory)).stat()
    except OSError:
        return source
    import hashlib

    source["revision"] = hashlib.sha256(
        f"{info.st_size}\x00{info.st_mtime_ns}".encode("ascii")
    ).hexdigest()
    return source


def _asset_reference(directory: str, path: str) -> dict[str, Any] | None:
    """Create a path-free handoff reference for a verified library asset."""

    if not isinstance(path, str) or not path.strip():
        return None
    try:
        root = Path(directory).expanduser().resolve()
        raw = Path(path).expanduser()
        candidate = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
        relative = candidate.relative_to(root).as_posix()
        if not candidate.is_file():
            return None
        info = candidate.stat()
        return photo_asset_reference(
            photo_library_id(root),
            relative,
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
        ).to_dict()
    except (OSError, RuntimeError, ValueError):
        return None


def _source_path(directory: str, path: str) -> str:
    """Resolve a user-facing relative image path within its selected library."""

    candidate = Path(path).expanduser()
    return str(candidate if candidate.is_absolute() else Path(directory).expanduser() / candidate)


def _decorate_results(results: list[dict[str, Any]], directory: str) -> list[dict[str, Any]]:
    """Attach optional asset identities without trusting result paths."""

    decorated: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        reference = _asset_reference(directory, item.get("path", ""))
        decorated.append({**item, **({"assetReference": reference} if reference else {})})
    return decorated


def _face_name_group(
    person: int | str = 0,
    name: str = "",
    *,
    directory: str = ".",
    threshold: float = 0.45,
    **_kw: Any,
) -> dict[str, Any]:
    try:
        result = _idx.name_face_group(person, name, db_path=_idx_db(directory), threshold=threshold)
    except ValueError as exc:
        return {"error": "invalid_person_label", "hint": str(exc)}
    if result is None:
        return {
            "error": "face_group_not_found",
            "hint": "请先执行 face_group_albums，并使用当前分组编号及相同 threshold 命名。",
        }
    return {**result, "source": _library_source(directory)}


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
        response = {
            "error": summary.get("error", "index_build_failed"),
            "hint": "请确认目录存在且包含图片文件，或 CLIP 模型可用。",
        }
        if summary.get("resource_limited") is True:
            response.update(
                {
                    "retryable": True,
                    "hint": "当前图片或视频推理资源正忙，旧索引已保留；稍后重试即可。",
                }
            )
        return response
    return {**summary, "source": _library_source(directory)}


def register_image_semantic_skills(registry: SkillRegistry) -> int:
    registry.register(
        Skill(
            name="image_index_build",
            description=(
                "建立/重建工作区内图片语义索引。扫描目录下的图片，用 CLIP 生成向量；"
                "设备 NAS 相册必须使用 photos_* 工具，不能把目录参数当作 NAS 授权。"
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
                "用文字描述在工作区图片库中检索语义最相近的图片（CLIP 文→图）。"
                "设备 NAS 相册使用 photos_search。"
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
                "用一张图片在工作区图片库中检索视觉最相似的图片（CLIP 图→图，以图搜图）。"
                "设备 NAS 相册使用 photos_search。"
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
                "把工作区索引图片中的人脸按身份聚类成人物分组（AI 相册的人脸分组）。"
                "Args: {directory?: string, threshold?: number}。"
                "需先执行 image_index_build（含人脸）建立索引。"
                "person 编号只属于当前索引快照；用 face_name_group 保存可跨重建检索的姓名。"
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
                "用一张图片在工作区图片库中检索包含同一人的图片（人脸以图搜人）。"
                "Args: {image_path: string, directory?: string, top_k?: int}。"
                "需先执行 image_index_build（含人脸）建立索引。"
            ),
            affinity=["image", "face", "search"],
            cost_profile="mid",
            trusted_source="skill://private/face_search_by_image",
            handler=_face_search_by_image,
        )
    )
    registry.register(
        Skill(
            name="face_name_group",
            description=(
                "给当前人脸分组命名，保存本地人脸原型供 image_filter_meta(person=姓名) 检索。"
                "先调用 face_group_albums，使用其 person 编号和相同 threshold；编号在重建后"
                "可能改变。Args: {person: int, name: string, directory?: string, threshold?: number}。"
                "同名会更新原型，只修改本地索引，不修改照片。"
            ),
            affinity=["image", "face", "album"],
            cost_profile="low",
            trusted_source="skill://private/face_name_group",
            handler=_face_name_group,
        )
    )
    return 6


__all__ = ["register_image_semantic_skills"]
