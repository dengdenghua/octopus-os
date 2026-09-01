"""Audio silence detection for atomic timeline edits."""

from __future__ import annotations

import math
from pathlib import Path


def detect_silences(
    path: Path,
    *,
    source_start: float,
    source_end: float,
    threshold_db: float = -40,
    min_silence_sec: float = 0.5,
    pad_sec: float = 0.1,
) -> list[tuple[float, float]]:
    try:
        import av
        import numpy as np
    except ImportError as exc:
        raise ValueError("audio analysis dependency is unavailable") from exc
    if source_end <= source_start:
        raise ValueError("clip source window is empty")
    threshold_db = max(-90.0, min(-1.0, float(threshold_db)))
    min_silence_sec = max(0.05, float(min_silence_sec))
    pad_sec = max(0.0, float(pad_sec))
    silent_ranges: list[tuple[float, float]] = []
    open_start: float | None = None
    cursor = source_start
    try:
        with av.open(str(path)) as container:
            stream = next((item for item in container.streams if item.type == "audio"), None)
            if stream is None:
                raise ValueError("media has no audio stream")
            if stream.time_base:
                container.seek(
                    max(0, int(source_start / float(stream.time_base))),
                    stream=stream,
                    backward=True,
                )
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
            done = False
            for frame in container.decode(stream):
                for mono in resampler.resample(frame):
                    start = float(mono.time if mono.time is not None else cursor)
                    duration = float(mono.samples) / 16000
                    end = start + duration
                    cursor = end
                    if end <= source_start:
                        continue
                    if start >= source_end:
                        done = True
                        break
                    segment_start = max(source_start, start)
                    values = mono.to_ndarray().astype("float32", copy=False).reshape(-1)
                    rms = float(np.sqrt(np.mean(np.square(values)))) if values.size else 0.0
                    db = 20 * math.log10(max(rms, 1e-9))
                    if db <= threshold_db:
                        if open_start is None:
                            open_start = segment_start
                    elif open_start is not None:
                        _close_silence(
                            silent_ranges,
                            open_start,
                            segment_start,
                            min_silence_sec,
                            pad_sec,
                        )
                        open_start = None
                if done:
                    break
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"cannot analyze audio: {path.name}") from exc
    if open_start is not None:
        _close_silence(
            silent_ranges,
            open_start,
            source_end,
            min_silence_sec,
            pad_sec,
        )
    return silent_ranges


def _close_silence(
    output: list[tuple[float, float]],
    start: float,
    end: float,
    minimum: float,
    padding: float,
) -> None:
    if end - start < minimum:
        return
    cut_start = start + padding
    cut_end = end - padding
    if cut_end - cut_start >= 0.01:
        output.append((cut_start, cut_end))


__all__ = ["detect_silences"]
