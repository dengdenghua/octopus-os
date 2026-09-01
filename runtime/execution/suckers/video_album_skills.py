"""Video album skills (local AI video library).

Wraps :mod:`runtime.memory.hemolymph.video_semantic_index` as agent tools for
the AI-album layer on top of the raw video semantic index:
  * ``video_index_build``     — build the video index (keyframes/faces/transcript)
  * ``video_search_by_text``  — find keyframes semantically closest to a text query
  * ``video_search_by_image`` — find keyframes visually closest to an image
  * ``video_search_by_face``  — find video keyframes containing the same face(s)
  * ``video_search_by_speech``— find transcript segments matching a text query
  * ``video_analyze``         — zero-shot classify one video (CLIP)
  * ``video_face_albums``     — cluster face embeddings into person groups

All seven are self-gating: when the underlying model / index isn't available,
they return a clear message instead of failing — so the agent degrades
gracefully.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .registry import Skill

if TYPE_CHECKING:
    from .registry import SkillRegistry

from runtime.memory.hemolymph import video_semantic_index as _vidx

from .image_album_skills import _idx_db  # reuse the directory-scoped DB helper


def _video_index_build(
    directory: str = ".",
    include_faces: bool = True,
    include_transcript: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    result = _vidx.build_video_index(
        directory,
        db_path=_idx_db(directory),
        include_faces=include_faces,
        include_transcript=include_transcript,
    )
    if result is None:
        return _not_ready("video indexing")
    return {"ok": bool(result.get("ok"))}


def _video_search_by_text(
    directory: str = ".",
    query: str = "",
    top_k: int = 10,
    **_kw: Any,
) -> dict[str, Any]:
    if not query.strip():
        return {"error": "missing query"}
    results = _vidx.search_video_by_text(
        query,
        db_path=_idx_db(directory),
        top_k=top_k,
    )
    if results is None:
        return _not_ready("video text search")
    return {"query": query, "results": results, "count": len(results)}


def _video_search_by_image(
    directory: str = ".",
    image_path: str = "",
    top_k: int = 10,
    **_kw: Any,
) -> dict[str, Any]:
    if not image_path:
        return {"error": "missing image_path"}
    results = _vidx.search_video_by_image(
        image_path,
        db_path=_idx_db(directory),
        top_k=top_k,
    )
    if results is None:
        return _not_ready("video image search")
    return {"image": image_path, "results": results, "count": len(results)}


def _video_search_by_face(
    directory: str = ".",
    image_path: str = "",
    top_k: int = 10,
    **_kw: Any,
) -> dict[str, Any]:
    if not image_path:
        return {"error": "missing image_path"}
    results = _vidx.search_face_in_videos(
        image_path,
        db_path=_idx_db(directory),
        top_k=top_k,
    )
    if results is None:
        return _not_ready("face search")
    return {"image": image_path, "results": results, "count": len(results)}


def _video_search_by_speech(
    directory: str = ".",
    query: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    if not query.strip():
        return {"error": "missing query"}
    results = _vidx.search_video_by_speech(query, db_path=_idx_db(directory))
    if results is None:
        return _not_ready("speech search")
    return {"query": query, "results": results, "count": len(results)}


def _video_analyze(
    directory: str = ".",
    video_path: str = "",
    labels: list[str] | None = None,
    top_k: int = 5,
    **_kw: Any,
) -> dict[str, Any]:
    if not video_path:
        return {"error": "missing video_path"}
    results = _vidx.classify_video(
        video_path,
        labels=labels,
        top_k=top_k,
        db_path=_idx_db(directory),
    )
    if results is None:
        return _not_ready("video classification")
    return {"video": video_path, "labels": labels, "results": results}


def _video_face_albums(
    directory: str = ".",
    threshold: float = 0.45,
    **_kw: Any,
) -> dict[str, Any]:
    groups = _vidx.group_video_faces(db_path=_idx_db(directory), threshold=threshold)
    if groups is None:
        return _not_ready("face album clustering")
    return {"groups": groups, "count": len(groups)}


def _not_ready(feature: str) -> dict[str, Any]:
    return {
        "error": "video_album_unavailable",
        "hint": f"请先执行 video_index_build 建立视频索引，或确认 {feature} 所需模型可用。",
        "results": [],
    }


def register_video_album_skills(registry: SkillRegistry) -> int:
    registry.register(
        Skill(
            name="video_index_build",
            description=(
                "为本地视频库建立索引（抽取关键帧、可选人脸向量与语音转写），"
                "之后各类 video_search_* / video_analyze 技能才能使用。"
                "Args: {directory?: string, include_faces?: bool, "
                "include_transcript?: bool}。"
            ),
            affinity=["video", "vision", "album", "index"],
            cost_profile="high",
            trusted_source="skill://private/video_index_build",
            handler=_video_index_build,
        )
    )
    registry.register(
        Skill(
            name="video_search_by_text",
            description=(
                "用文本描述在本地视频库中检索最相似的视频关键帧（CLIP 文→图），"
                "返回视频路径、时间点与相似度。Args: {query: string, directory?: string, "
                "top_k?: int}。需先执行 video_index_build 建立索引。"
            ),
            affinity=["video", "vision", "search", "text"],
            cost_profile="mid",
            trusted_source="skill://private/video_search_by_text",
            handler=_video_search_by_text,
        )
    )
    registry.register(
        Skill(
            name="video_search_by_image",
            description=(
                "用一张图片在本地视频库中检索视觉上最相似的关键帧，返回视频路径、"
                "时间点与相似度。Args: {image_path: string, directory?: string, "
                "top_k?: int}。需先执行 video_index_build 建立索引。"
            ),
            affinity=["video", "vision", "search", "image"],
            cost_profile="mid",
            trusted_source="skill://private/video_search_by_image",
            handler=_video_search_by_image,
        )
    )
    registry.register(
        Skill(
            name="video_search_by_face",
            description=(
                "用一张包含人脸的照片在本地视频库中检索包含同一人脸的视频关键帧，"
                "返回视频路径、时间点与相似度。Args: {image_path: string, "
                "directory?: string, top_k?: int}。需先执行 video_index_build "
                "（含 include_faces）建立索引。"
            ),
            affinity=["video", "vision", "search", "face"],
            cost_profile="mid",
            trusted_source="skill://private/video_search_by_face",
            handler=_video_search_by_face,
        )
    )
    registry.register(
        Skill(
            name="video_search_by_speech",
            description=(
                "在已转写语音的本地视频库中按文本匹配检索语音片段，返回视频路径、"
                "起止时间与匹配文本。Args: {query: string, directory?: string}。"
                "需先执行 video_index_build（含 include_transcript）建立索引。"
            ),
            affinity=["video", "audio", "search", "speech"],
            cost_profile="mid",
            trusted_source="skill://private/video_search_by_speech",
            handler=_video_search_by_speech,
        )
    )
    registry.register(
        Skill(
            name="video_analyze",
            description=(
                "对本地视频做零样本分类（CLIP 文→图，按关键帧平均），返回 Top-k 标签"
                "及置信度，支持自定义类别标签。Args: {video_path: string, "
                "directory?: string, labels?: string[], top_k?: int}。"
                "需先执行 video_index_build 建立索引。"
            ),
            affinity=["video", "vision", "album", "classify"],
            cost_profile="mid",
            trusted_source="skill://private/video_analyze",
            handler=_video_analyze,
        )
    )
    registry.register(
        Skill(
            name="video_face_albums",
            description=(
                "在本地视频库中把人脸嵌入聚类成人物分组，返回各人物出现的视频与时间点。"
                "Args: {directory?: string, threshold?: number}。"
                "需先执行 video_index_build（含 include_faces）建立索引。"
            ),
            affinity=["video", "vision", "album", "face"],
            cost_profile="high",
            trusted_source="skill://private/video_face_albums",
            handler=_video_face_albums,
        )
    )
    return 7


__all__ = ["register_video_album_skills"]
