from __future__ import annotations

import shutil
from pathlib import Path

from benchmarks.trusted_verifier_controller import UnsafeLocalWorkerLauncher
from benchmarks.verifiers import verify_concurrent_cache, verify_path_boundary

REPO_ROOT = Path(__file__).resolve().parents[1]


def _verify(script: str, workspace: Path) -> dict[str, object]:
    launcher = UnsafeLocalWorkerLauncher()
    if script == "verify_concurrent_cache.py":
        return verify_concurrent_cache._run(workspace, launcher=launcher)
    if script == "verify_path_boundary.py":
        return verify_path_boundary._run(workspace, launcher=launcher)
    raise AssertionError(f"unexpected verifier: {script}")


def test_concurrent_cache_fixture_has_a_satisfiable_hidden_verifier(tmp_path) -> None:
    workspace = tmp_path / "cache"
    shutil.copytree(REPO_ROOT / "benchmarks" / "fixtures" / "coding.concurrent-cache", workspace)
    (workspace / "cache.py").write_text(
        """
from __future__ import annotations
import threading
import time

class TTLCache:
    def __init__(self, ttl_seconds, *, clock=time.monotonic):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._values = {}
        self._conditions = {}
        self._lock = threading.Lock()

    def get_or_load(self, key, loader):
        while True:
            with self._lock:
                cached = self._values.get(key)
                if cached is not None and cached[0] > self._clock():
                    return cached[1]
                condition = self._conditions.get(key)
                if condition is None:
                    condition = threading.Condition(self._lock)
                    self._conditions[key] = condition
                    break
                condition.wait()
        try:
            value = loader()
        except BaseException:
            with self._lock:
                self._conditions.pop(key).notify_all()
            raise
        with self._lock:
            self._values[key] = (self._clock() + self.ttl_seconds, value)
            self._conditions.pop(key).notify_all()
        return value
""".lstrip(),
        encoding="utf-8",
    )
    (workspace / "tests" / "test_cache.py").write_text("def test_regression(): assert True\n")

    result = _verify("verify_concurrent_cache.py", workspace)

    assert result["passed"] is True, result


def test_concurrent_cache_verifier_rejects_unrelated_diff(tmp_path) -> None:
    workspace = tmp_path / "cache"
    shutil.copytree(REPO_ROOT / "benchmarks" / "fixtures" / "coding.concurrent-cache", workspace)
    (workspace / "conftest.py").write_text("# unrelated\n", encoding="utf-8")

    result = _verify("verify_concurrent_cache.py", workspace)

    assert result["passed"] is False
    assert "unrelated files" in str(result["reason"])


def test_concurrent_cache_verifier_loads_dataclass_generic_candidate(tmp_path) -> None:
    """Candidates using ``@dataclass`` on a ``Generic`` must not crash the
    verifier's module loader (the loader must register the module in
    ``sys.modules`` so dataclass annotation introspection can resolve it)."""
    workspace = tmp_path / "cache"
    shutil.copytree(REPO_ROOT / "benchmarks" / "fixtures" / "coding.concurrent-cache", workspace)
    (workspace / "cache.py").write_text(
        """
from __future__ import annotations
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

V = TypeVar("V")


@dataclass
class _Pending(Generic[V]):
    event: threading.Event = field(default_factory=threading.Event)
    result: V | None = None
    error: BaseException | None = None


class TTLCache(Generic[V]):
    def __init__(
        self,
        ttl_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._values: dict[str, tuple[float, V]] = {}
        self._pending: dict[str, _Pending[V]] = {}
        self._lock = threading.Lock()

    def get_or_load(self, key: str, loader: Callable[[], V]) -> V:
        while True:
            with self._lock:
                live = self._values.get(key)
                if live is not None and live[0] > self._clock():
                    return live[1]
                pending = self._pending.get(key)
                if pending is None:
                    pending = _Pending()
                    self._pending[key] = pending
                    break
            pending.event.wait(timeout=10)
        try:
            value = loader()
        except BaseException as exc:
            with self._lock:
                self._pending.pop(key, None)
                pending.error = exc
                pending.event.set()
            raise
        with self._lock:
            self._values[key] = (self._clock() + self.ttl_seconds, value)
            self._pending.pop(key, None)
            pending.event.set()
        return value
""".lstrip(),
        encoding="utf-8",
    )
    (workspace / "tests" / "test_cache.py").write_text("def test_regression(): assert True\n")

    result = _verify("verify_concurrent_cache.py", workspace)

    assert result["passed"] is True, result


def test_path_boundary_fixture_has_a_satisfiable_hidden_verifier(tmp_path) -> None:
    workspace = tmp_path / "paths"
    shutil.copytree(REPO_ROOT / "benchmarks" / "fixtures" / "coding.path-boundary", workspace)
    (workspace / "file_service.py").write_text(
        """
from __future__ import annotations
from pathlib import Path
from urllib.parse import unquote

class PathBoundaryError(ValueError):
    pass

class FileService:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def read_text(self, user_path: str) -> str:
        decoded = user_path
        for _ in range(4):
            updated = unquote(decoded)
            if updated == decoded:
                break
            decoded = updated
        root = self.root.resolve()
        candidate = (root / decoded).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PathBoundaryError("path escapes root") from exc
        return candidate.read_text(encoding="utf-8")
""".lstrip(),
        encoding="utf-8",
    )
    (workspace / "tests" / "test_file_service.py").write_text(
        "def test_regression(): assert True\n"
    )

    result = _verify("verify_path_boundary.py", workspace)

    assert result["passed"] is True, result

