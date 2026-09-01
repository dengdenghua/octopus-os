from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

from ..events import GitCommitDetected
from ..sensor import EnvSensor, SensorStatus

logger = logging.getLogger(__name__)


class GitHookSensor(EnvSensor):
    def __init__(
        self,
        *,
        repo_root: str | Path,
        poll_interval_seconds: float = 30.0,
        sensor_id: str = "git_hook",
    ) -> None:
        super().__init__()
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        self.sensor_id = sensor_id
        self.repo_root = Path(repo_root).resolve()
        self.poll_interval = poll_interval_seconds

        self._last_sha: str = ""
        self._bg_thread: threading.Thread | None = None
        self._stop_evt = threading.Event()

    def _git(self, *args: str) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", str(self.repo_root), *args],
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
            if r.returncode != 0:
                self._last_error = f"git {args[0]}: {r.stderr.strip()[:200]}"
                return ""
            return r.stdout.strip()
        except (subprocess.TimeoutExpired, OSError) as e:
            self._last_error = f"{type(e).__name__}: {e}"
            return ""

    def _current_sha(self) -> str:
        return self._git("rev-parse", "HEAD")

    def _current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD")

    def _commit_meta(self, sha: str) -> tuple[str, str]:
        if not sha:
            return ("", "")
        out = self._git("log", "-1", "--format=%an|||%s", sha)
        if "|||" not in out:
            return ("", "")
        a, s = out.split("|||", 1)
        return (a, s)

    def check_once(self) -> GitCommitDetected | None:
        sha = self._current_sha()
        if not sha or sha == self._last_sha:
            return None
        old_sha = self._last_sha
        self._last_sha = sha
        author, subject = self._commit_meta(sha)
        branch = self._current_branch()
        evt = GitCommitDetected(
            repo_path=str(self.repo_root),
            sha=sha,
            old_sha=old_sha,
            branch=branch,
            author=author,
            subject=subject,
        )
        try:
            self._publish(evt)
        except Exception as e:  # noqa: BLE001
            self._last_error = f"{type(e).__name__}: {e}"
        return evt

    # ─── lifecycle ────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self._stop_evt.clear()

        self._last_sha = self._current_sha()

        self._bg_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name=f"skin-{self.sensor_id}",
        )
        self._bg_thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        self._stop_evt.set()
        t = self._bg_thread
        self._bg_thread = None
        if t is not None and t.is_alive():
            t.join(timeout=2.0)

    def _poll_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.check_once()
            except (OSError, TypeError, ValueError, RuntimeError):
                logger.exception("GitHookSensor poll loop error")
            self._stop_evt.wait(self.poll_interval)

    def status(self) -> SensorStatus:
        base = super().status()
        return SensorStatus(
            sensor_id=base.sensor_id,
            running=base.running,
            events_emitted=base.events_emitted,
            last_emit_at=base.last_emit_at,
            last_error=base.last_error,
            metadata={
                "repo_root": str(self.repo_root),
                "last_sha": self._last_sha,
            },
        )
