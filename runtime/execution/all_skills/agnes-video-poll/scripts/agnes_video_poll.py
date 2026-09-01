"""Standalone skill entry for polling an Agnes video task.

Thin wrapper around ``poll_video()`` from the agnes-video-generate
skill, exposed as its own first-class skill so the model can find it
via skill catalog / @skill: mention without needing to know to call
the parent generate function with extra args.

Typical flow:
    1. agnes-video-generate(prompt=...) → returns {"task_id": "xyz", "status": "queued"}
    2. (model continues conversation; render takes 30-180s)
    3. user: "is my video done yet?"
    4. agnes-video-poll(task_id="xyz") → {"status": "completed", "video_url": "..."}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# The generate skill ships poll_video() — reuse it instead of
# duplicating the HTTP plumbing. Resolve the sibling skill's scripts
# dir at import time so this module is runnable both from inside the
# echo runtime AND as a CLI smoke test.
_GENERATE_SCRIPTS = (
    Path(__file__).resolve().parent.parent.parent / "agnes-video-generate" / "scripts"
)
if str(_GENERATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_GENERATE_SCRIPTS))

from agnes_video_generate import poll_video  # noqa: E402


def poll_agnes_video(
    task_id: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """One-shot status poll for an Agnes video generation task.

    Parameters
    ----------
    task_id
        Task id from a prior ``agnes-video-generate`` call.
    api_key, base_url
        Optional config overrides. Defaults to ``AGNES_API_KEY`` env
        var and ``https://apihub.agnes-ai.com/v1``.

    Returns
    -------
    dict
        Contains ``task_id``, ``status``, ``model``, ``progress``.
        On ``status == "completed"`` the ``video_url`` field is set.
        On ``status == "failed"`` the ``error`` field is set.

    Raises
    ------
    ValueError
        If ``task_id`` is empty or no API key resolved.
    RuntimeError
        Non-200 from the gateway.
    """
    return poll_video(task_id, api_key=api_key, base_url=base_url)


__all__ = ["poll_agnes_video"]


if __name__ == "__main__":  # pragma: no cover — manual smoke test
    if len(sys.argv) < 2:
        print(
            "usage: agnes_video_poll.py <task_id>",
            file=sys.stderr,
        )
        sys.exit(1)
    print(json.dumps(poll_agnes_video(sys.argv[1]), ensure_ascii=False, indent=2))
