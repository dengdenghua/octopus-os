from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from runtime.adapters.instrumentation import trace_stage
from runtime.platform.models import now_utc


class BackendAudit(BaseModel):
    model_config = ConfigDict(frozen=True)

    arm_id: str
    allowed_paths: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(default_factory=list)
    started_at: Any = Field(default_factory=now_utc)


class LocalBackend:
    def __init__(
        self,
        allowed_read_roots: list[Path] | None = None,
        allowed_write_roots: list[Path] | None = None,
    ) -> None:
        self.allowed_read_roots = [Path(p).resolve() for p in (allowed_read_roots or [])]
        self.allowed_write_roots = [Path(p).resolve() for p in (allowed_write_roots or [])]

    @contextmanager
    def sandbox(self, arm_id: str) -> Iterator[Sandbox]:
        with trace_stage("backend.sandbox", arm_id=arm_id) as span:
            audit = BackendAudit(
                arm_id=arm_id,
                allowed_paths=[str(p) for p in self.allowed_read_roots],
            )
            box = Sandbox(backend=self, audit=audit, span=span)
            try:
                yield box
            finally:
                span.set_attribute("echo.backend.allowed_count", len(box._allowed_hits))
                span.set_attribute("echo.backend.denied_count", len(box._denied_hits))

    def allows_read(self, path: Path) -> bool:
        if not self.allowed_read_roots:
            return True
        resolved = Path(path).resolve()
        return any(self._is_within(resolved, root) for root in self.allowed_read_roots)

    def allows_write(self, path: Path) -> bool:
        if not self.allowed_write_roots:
            return False  # Implementation note.
        resolved = Path(path).resolve()
        return any(self._is_within(resolved, root) for root in self.allowed_write_roots)

    @staticmethod
    def _is_within(child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False


class Sandbox:
    def __init__(self, backend: LocalBackend, audit: BackendAudit, span: Any) -> None:
        self.backend = backend
        self.audit = audit
        self._span = span
        self._allowed_hits: list[str] = []
        self._denied_hits: list[str] = []

    def run(self, handler: Callable[..., Any], **kwargs: Any) -> Any:
        return handler(box=self, **kwargs)

    def check_read(self, path: str | Path) -> Path:
        p = Path(path).resolve()
        if self.backend.allows_read(p):
            self._allowed_hits.append(str(p))
            return p
        self._denied_hits.append(str(p))
        raise PermissionError(f"backend denied read: {p}")

    def check_write(self, path: str | Path) -> Path:
        p = Path(path).resolve()
        if self.backend.allows_write(p):
            self._allowed_hits.append(str(p))
            return p
        self._denied_hits.append(str(p))
        raise PermissionError(f"backend denied write: {p}")
