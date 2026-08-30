"""Encode a Clip Studio timeline into a playable local MP4."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

import av
import numpy as np

from .snapshot_renderer import render_composite_frame


def encode_project_video(
    project: dict[str, Any],
    output_dir: Path,
    *,
    max_dim: int = 1280,
    include_audio: bool = True,
) -> dict[str, Any]:
    settings = project.get("settings", {})
    fps = max(1, min(60, int(settings.get("frameRate") or 30)))
    duration = _duration(project)
    if duration <= 0:
        raise ValueError("project has no timeline content")
    if duration > 120:
        raise ValueError("export is limited to 120 seconds")
    max_dim = max(160, min(1280, int(max_dim)))
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "export.mp4"
    temporary = output_path.with_suffix(".mp4.tmp")
    warnings: list[dict[str, Any]] = []

    first, _clip, first_warnings = render_composite_frame(project, 0, max_dim)
    warnings.extend(first_warnings)
    width = first.width - first.width % 2
    height = first.height - first.height % 2
    if width < 2 or height < 2:
        raise ValueError("export dimensions are invalid")

    try:
        with av.open(str(temporary), mode="w", format="mp4") as container:
            video = _video_stream(container, fps, width, height)
            mixed: np.ndarray | None = None
            audio = None
            if include_audio:
                try:
                    mixed = _mix_audio(project, duration)
                    if mixed is not None:
                        audio = container.add_stream("aac", rate=48_000)
                        audio.layout = "stereo"
                        audio.bit_rate = 192_000
                except (av.error.FFmpegError, OSError, ValueError) as exc:
                    warnings.append({"kind": "audio_export_failed", "detail": str(exc)})
            frame_count = max(1, int(round(duration * fps)))
            audio_start = 0
            for index in range(frame_count):
                at_sec = min(duration - 1e-6, index / fps)
                if index == 0:
                    image = first
                else:
                    image, _active, frame_warnings = render_composite_frame(
                        project, at_sec, max_dim
                    )
                    warnings.extend({"atSec": at_sec, **item} for item in frame_warnings)
                if image.size != (width, height):
                    image = image.resize((width, height))
                frame = av.VideoFrame.from_image(image.convert("RGB"))
                frame.pts = index
                frame.time_base = Fraction(1, fps)
                for packet in video.encode(frame):
                    container.mux(packet)
                if audio is not None and mixed is not None:
                    audio_target = min(mixed.shape[1], round((index + 1) * 48_000 / fps))
                    while audio_start < audio_target:
                        audio_start = _encode_audio_chunk(container, audio, mixed, audio_start)
            for packet in video.encode():
                container.mux(packet)
            if audio is not None and mixed is not None:
                while audio_start < mixed.shape[1]:
                    audio_start = _encode_audio_chunk(container, audio, mixed, audio_start)
                for packet in audio.encode():
                    container.mux(packet)
        temporary.replace(output_path)
    except (av.error.FFmpegError, OSError) as exc:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"cannot encode export: {exc}") from exc

    return {
        "ok": True,
        "path": str(output_path.resolve()),
        "durationSec": duration,
        "frameRate": fps,
        "width": width,
        "height": height,
        "size": output_path.stat().st_size,
        "warnings": warnings,
    }


def _video_stream(container: av.container.OutputContainer, fps: int, width: int, height: int):
    try:
        stream = container.add_stream("libx264", rate=fps)
    except av.error.FFmpegError:
        stream = container.add_stream("mpeg4", rate=fps)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    stream.bit_rate = min(12_000_000, max(1_500_000, width * height * fps // 3))
    return stream


def _encode_audio_chunk(
    container: av.container.OutputContainer,
    stream: Any,
    mixed: np.ndarray,
    start: int,
) -> int:
    end = min(mixed.shape[1], start + 1024)
    chunk = np.ascontiguousarray(mixed[:, start:end])
    audio_frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
    audio_frame.sample_rate = 48_000
    audio_frame.pts = start
    audio_frame.time_base = Fraction(1, 48_000)
    for packet in stream.encode(audio_frame):
        container.mux(packet)
    return end


def _duration(project: dict[str, Any]) -> float:
    return max(
        (
            float(clip.get("endSec") or 0)
            for track in project.get("tracks", [])
            for clip in track.get("clips", [])
        ),
        default=0.0,
    )


def _mix_audio(project: dict[str, Any], duration: float) -> np.ndarray | None:
    sample_rate = 48_000
    total = max(1, int(round(duration * sample_rate)))
    mix = np.zeros((2, total), dtype=np.float32)
    found = False
    audio_tracks = [
        track
        for track in project.get("tracks", [])
        if track.get("type") in {"audio", "video"} and not track.get("muted")
    ]
    solo = any(track.get("solo") for track in audio_tracks)
    if solo:
        audio_tracks = [track for track in audio_tracks if track.get("solo")]
    media_by_id = {str(item.get("id")): item for item in project.get("media", [])}
    for track in audio_tracks:
        for clip in track.get("clips", []):
            media = media_by_id.get(str(clip.get("mediaId") or ""))
            raw_path = str((media or {}).get("path") or "")
            if not raw_path:
                continue
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = (Path.cwd() / path).resolve()
            if not path.is_file():
                continue
            decoded = _decode_audio(path, sample_rate)
            if decoded is None or not decoded.shape[1]:
                continue
            source_in = max(0, int(float(clip.get("sourceInSec") or 0) * sample_rate))
            source_out = min(
                decoded.shape[1],
                int(
                    float(clip.get("sourceOutSec") or decoded.shape[1] / sample_rate) * sample_rate
                ),
            )
            segment = decoded[:, source_in:source_out]
            speed = max(0.1, float(clip.get("speed") or 1))
            timeline_length = max(
                1,
                int(
                    (float(clip.get("endSec") or 0) - float(clip.get("startSec") or 0))
                    * sample_rate
                ),
            )
            if segment.shape[1] != timeline_length:
                positions = np.linspace(0, max(0, segment.shape[1] - 1), timeline_length)
                if speed:
                    segment = np.vstack(
                        [
                            np.interp(positions, np.arange(segment.shape[1]), channel)
                            for channel in segment
                        ]
                    ).astype(np.float32)
            if clip.get("reverse"):
                segment = segment[:, ::-1]
            start = max(0, int(float(clip.get("startSec") or 0) * sample_rate))
            end = min(total, start + segment.shape[1])
            if end <= start:
                continue
            volume = max(0.0, min(2.0, float(clip.get("volume", 1))))
            mix[:, start:end] += segment[:, : end - start] * volume
            found = True
    if not found:
        return None
    return np.clip(mix, -1.0, 1.0)


def _decode_audio(path: Path, sample_rate: int) -> np.ndarray | None:
    chunks: list[np.ndarray] = []
    with av.open(str(path)) as container:
        stream = next((item for item in container.streams if item.type == "audio"), None)
        if stream is None:
            return None
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=sample_rate)
        for frame in container.decode(stream):
            converted = resampler.resample(frame)
            for item in converted if isinstance(converted, list) else [converted]:
                if item is not None:
                    chunks.append(item.to_ndarray().astype(np.float32, copy=False))
        flushed = resampler.resample(None)
        for item in flushed if isinstance(flushed, list) else [flushed]:
            if item is not None:
                chunks.append(item.to_ndarray().astype(np.float32, copy=False))
    return np.concatenate(chunks, axis=1) if chunks else None


__all__ = ["encode_project_video"]
