from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from runtime.core.nerves.bus import NervesEvent


class SensorEvent(NervesEvent):
    sensor_id: str = ""
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FileChanged(SensorEvent):
    path: str
    change_type: Literal["created", "modified", "deleted", "moved"]
    old_path: str = ""  # Implementation note.
    size_bytes: int = -1  # Implementation note.


class DirectoryChanged(SensorEvent):
    path: str
    change_type: Literal["created", "deleted"]


class GitCommitDetected(SensorEvent):
    repo_path: str
    sha: str
    old_sha: str = ""
    branch: str = ""
    author: str = ""
    subject: str = ""


class ProcessStateChanged(SensorEvent):
    name: str
    pid: int | None = None
    state: Literal["started", "stopped", "crashed", "running"]
    exit_code: int | None = None


class EnvironmentPing(SensorEvent):
    cpu_percent: float = -1.0
    mem_percent: float = -1.0
    active_sensor_count: int = 0
