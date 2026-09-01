"""Image album skills (local AI photo library).

Wraps :mod:`runtime.memory.hemolymph.image_semantic_index` as agent tools for
the AI-album layer on top of the raw semantic index:
  * ``image_analyze``       — zero-shot classify one image (CLIP)
  * ``image_ocr``           — OCR the text inside one image
  * ``image_find_duplicates`` — find near-duplicate images by perceptual hash
  * ``image_find_blurry``   — find images below a sharpness threshold
  * ``image_sensitive_scan`` — flag images that may contain sensitive content
  * ``image_filter_meta``   — filter indexed images by metadata
  * ``image_train_category`` — train a custom album category from examples

All seven are self-gating: when the underlying model / index isn't available
or ``ECHO_IMAGE_SEMANTIC=0``, they return a clear message instead of
failing — so the agent degrades gracefully.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .registry import Skill

if TYPE_CHECKING:
    from .registry import SkillRegistry

from runtime.memory.hemolymph import image_semantic_index as _idx

from .image_semantic_skills import _idx_db  # reuse the directory-scoped DB helper


def _image_analyze(
    directory: str = ".",
    image_path: str = "",
    labels: list[str] | None = None,
    top_k: int = 5,
    **_kw: Any,
) -> dict[str, Any]:
    if not image_path:
        return {"error": "missing image_path"}
    results = _idx.classify_image(
        image_path,
        labels=labels,
        top_k=top_k,
        db_path=_idx_db(directory),
    )
    if results is None:
        return _not_ready("image classification")
    return {"image": image_path, "labels": labels, "results": results}


def _image_ocr(
    directory: str = ".",
    image_path: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    if not image_path:
        return {"error": "missing image_path"}
    result = _idx.ocr_image(image_path, db_path=_idx_db(directory))
    if result is None:
        return _not_ready("OCR")
    return {
        "image": image_path,
        "text": result.get("text", ""),
        "confidence": result.get("confidence"),
        "boxes": result.get("boxes", []),
    }


def _image_find_duplicates(
    directory: str = ".",
    hash_threshold: int = 4,
    **_kw: Any,
) -> dict[str, Any]:
    groups = _idx.find_duplicates(db_path=_idx_db(directory), hash_threshold=hash_threshold)
    if groups is None:
        return _not_ready("duplicate detection")
    return {"groups": groups, "count": len(groups)}


def _image_find_blurry(
    directory: str = ".",
    threshold: float = 50.0,
    **_kw: Any,
) -> dict[str, Any]:
    blurry = _idx.find_blurry(db_path=_idx_db(directory), threshold=threshold)
    if blurry is None:
        return _not_ready("blur detection")
    return {"blurry": blurry, "count": len(blurry)}


def _image_sensitive_scan(
    directory: str = ".",
    top_k: int = 5,
    **_kw: Any,
) -> dict[str, Any]:
    flagged = _idx.sensitive_scan(db_path=_idx_db(directory), top_k=top_k)
    if flagged is None:
        return _not_ready("sensitive-content scan")
    return {"flagged": flagged, "count": len(flagged)}


def _image_filter_meta(
    directory: str = ".",
    year: int | None = None,
    month: int | None = None,
    file_type: str | None = None,
    location: str | None = None,
    min_width: int | None = None,
    min_height: int | None = None,
    person: str | None = None,
    scene: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    matches = _idx.filter_meta(
        db_path=_idx_db(directory),
        year=year,
        month=month,
        file_type=file_type,
        location=location,
        min_width=min_width,
        min_height=min_height,
        person=person,
        scene=scene,
    )
    if matches is None:
        return _not_ready("metadata filtering")
    return {"matches": matches, "count": len(matches)}


def _image_train_category(
    directory: str = ".",
    name: str = "",
    image_paths: list[str] | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    if not name.strip():
        return {"error": "missing category name"}
    if not image_paths:
        return {"error": "missing image_paths"}
    result = _idx.train_category(name, image_paths, db_path=_idx_db(directory))
    if result is None:
        return _not_ready("category training")
    return {
        "name": result.get("name"),
        "examples": result.get("examples"),
        "vector_dim": result.get("vector_dim"),
    }


def _not_ready(feature: str) -> dict[str, Any]:
    return {
        "error": "image_album_unavailable",
        "hint": f"请先执行 image_index_build 建立图片索引，或确认 {feature} 所需模型可用。",
        "results": [],
    }


def register_image_album_skills(registry: SkillRegistry) -> int:
    registry.register(
        Skill(
            name="image_analyze",
            description=(
                "对本地图片做零样本分类（CLIP 文→图），返回 Top-k 标签及置信度，"
                "支持自定义类别标签。Args: {image_path: string, directory?: string, "
                "labels?: string[], top_k?: int}。需先执行 image_index_build 建立索引。"
            ),
            affinity=["image", "vision", "album", "classify"],
            cost_profile="mid",
            trusted_source="skill://private/image_analyze",
            handler=_image_analyze,
        )
    )
    registry.register(
        Skill(
            name="image_ocr",
            description=(
                "对本地图片做 OCR 文字识别（rapidocr），返回识别文本、置信度与文本框坐标。"
                "Args: {image_path: string, directory?: string}。"
                "需先执行 image_index_build 建立索引。"
            ),
            affinity=["image", "vision", "album", "ocr"],
            cost_profile="mid",
            trusted_source="skill://private/image_ocr",
            handler=_image_ocr,
        )
    )
    registry.register(
        Skill(
            name="image_find_duplicates",
            description=(
                "在本地图片库中按感知哈希找出近似重复的图片，返回分组、成员与代表图。"
                "Args: {directory?: string, hash_threshold?: int}。"
                "需先执行 image_index_build 建立索引。"
            ),
            affinity=["image", "vision", "album", "duplicate"],
            cost_profile="mid",
            trusted_source="skill://private/image_find_duplicates",
            handler=_image_find_duplicates,
        )
    )
    registry.register(
        Skill(
            name="image_find_blurry",
            description=(
                "在本地图片库中找出低于锐度阈值（默认 50.0）的模糊图片。"
                "Args: {directory?: string, threshold?: number}。"
                "需先执行 image_index_build 建立索引。"
            ),
            affinity=["image", "vision", "album", "quality"],
            cost_profile="mid",
            trusted_source="skill://private/image_find_blurry",
            handler=_image_find_blurry,
        )
    )
    registry.register(
        Skill(
            name="image_sensitive_scan",
            description=(
                "扫描本地图片库，返回可能包含敏感内容（按标签与分数）的图片。"
                "Args: {directory?: string, top_k?: int}。"
                "需先执行 image_index_build 建立索引。"
            ),
            affinity=["image", "vision", "album", "safety"],
            cost_profile="high",
            trusted_source="skill://private/image_sensitive_scan",
            handler=_image_sensitive_scan,
        )
    )
    registry.register(
        Skill(
            name="image_filter_meta",
            description=(
                "按元数据过滤本地图片库：年/月/文件类型/地点/最小宽高/人物/场景。"
                "Args: {directory?: string, year?: int, month?: int, file_type?: string, "
                "location?: string, min_width?: int, min_height?: int, person?: string, "
                "scene?: string}。需先执行 image_index_build 建立索引。"
            ),
            affinity=["image", "vision", "album", "filter"],
            cost_profile="mid",
            trusted_source="skill://private/image_filter_meta",
            handler=_image_filter_meta,
        )
    )
    registry.register(
        Skill(
            name="image_train_category",
            description=(
                "用一组示例图片训练一个自定义相册类别（生成类别向量），之后可被"
                "image_analyze 的零样本分类识别。Args: {name: string, image_paths: string[], "
                "directory?: string}。需先执行 image_index_build 建立索引。"
            ),
            affinity=["image", "vision", "album", "train"],
            cost_profile="high",
            trusted_source="skill://private/image_train_category",
            handler=_image_train_category,
        )
    )
    return 7


__all__ = ["register_image_album_skills"]
