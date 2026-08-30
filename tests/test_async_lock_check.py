"""async_lock_check — the no-await-under-a-sync-lock ratchet.

Locks in the regression it was built for: a ``threading.Lock`` held across
``await`` (the team-rooms WS deadlock). Verifies the true positive fires and
the two safe shapes (``async with`` on an asyncio.Lock, an ``await`` only
inside a nested coroutine) do NOT.
"""

import ast
import subprocess
import sys
from pathlib import Path

from tools.lint.async_lock_check import _body_has_await, _is_lock_with, _scan

_REPO = Path(__file__).resolve().parent.parent


def _first_with(src: str) -> ast.With:
    """Return the first plain ``with`` node in ``src``."""
    tree = ast.parse(src)
    return next(n for n in ast.walk(tree) if isinstance(n, ast.With))


class TestLockDetection:
    def test_plain_lock_name_is_a_lock(self):
        assert _is_lock_with(_first_with("with lock:\n    pass\n")) is True

    def test_attribute_lock_is_a_lock(self):
        assert _is_lock_with(_first_with("with self._lock:\n    pass\n")) is True

    def test_mutex_is_a_lock(self):
        assert _is_lock_with(_first_with("with mutex:\n    pass\n")) is True

    def test_non_lock_context_is_not(self):
        assert _is_lock_with(_first_with("with open('x') as f:\n    pass\n")) is False


class TestAwaitUnderLock:
    def test_direct_await_is_flagged(self):
        node = _first_with("with lock:\n    await ws.send(x)\n")
        assert _body_has_await(node) is True

    def test_no_await_is_clean(self):
        node = _first_with("with lock:\n    teams[k] = v\n")
        assert _body_has_await(node) is False

    def test_await_in_nested_coroutine_is_ignored(self):
        # The await runs when `inner` is later scheduled — NOT while the lock
        # is held — so it must not count.
        node = _first_with(
            "with lock:\n    async def inner():\n        await something()\n    register(inner)\n"
        )
        assert _body_has_await(node) is False


class TestScanFixtures:
    def test_scan_flags_only_the_sync_lock_site(self, tmp_path: Path, monkeypatch):
        bad = tmp_path / "runtime" / "probe.py"
        bad.parent.mkdir(parents=True)
        bad.write_text(
            "import asyncio\n"
            "from threading import Lock\n"
            "lock = Lock()\n"
            "async def h(ws):\n"
            "    with lock:\n"  # ← line 5: the only violation
            "        await ws.send(1)\n"
            "async def safe(ws, alock: asyncio.Lock):\n"
            "    async with alock:\n"  # asyncio.Lock → never flagged
            "        await ws.send(1)\n",
            encoding="utf-8",
        )
        # Point the scanner at the tmp tree.
        import tools.lint.async_lock_check as mod

        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(mod, "_SCAN_ROOTS", ("runtime",))
        hits = _scan()
        assert hits == {"runtime/probe.py:5": 5}


class TestRealTreeIsClean:
    def test_strict_passes_on_the_repo(self):
        # The whole tree obeys the rule (baseline is empty); --strict is green.
        result = subprocess.run(
            [sys.executable, "tools/lint/async_lock_check.py", "--strict"],
            cwd=_REPO,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "OK" in result.stdout

