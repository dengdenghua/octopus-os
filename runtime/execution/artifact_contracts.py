"""Host-owned artifact coordinates shared by execution providers.

The model may describe an output, but it must not decide which workspace,
baseline or ownership record is authoritative.  These small immutable value
objects let Native, Codex and future providers exchange bounded artifact
references without copying file contents into transport metadata.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """A host-verified file reference, never a model-authored claim."""

    path: Path
    sha256: str
    size: int
    producer_task_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "size": self.size,
            "producer_task_id": self.producer_task_id,
        }


@dataclass(frozen=True, slots=True)
class ArtifactContract:
    """Declared inputs and outputs for a provider handoff.

    The contract carries coordinates only.  The host remains responsible for
    hashing inputs before dispatch and verifying outputs after completion.
    """

    inputs: tuple[ArtifactReference, ...]
    output_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class HandoffRecorder:
    """Host-installed durable journal callbacks.

    Callbacks are kept as Python objects and are never accepted from model or
    transport JSON.  Consumers that reconcile an interrupted handoff require
    ``read`` and fail closed when it is absent.
    """

    write: Callable[[dict[str, Any]], None] = field(repr=False)
    read: Callable[[], tuple[dict[str, Any], ...]] | None = field(
        default=None,
        repr=False,
    )


__all__ = ["ArtifactContract", "ArtifactReference", "HandoffRecorder"]
