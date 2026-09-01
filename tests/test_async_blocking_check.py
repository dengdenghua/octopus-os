"""async_blocking_check — the no-blocking-call-in-async-flow ratchet.

Pins the true positives (``time.sleep`` / ``requests`` inside ``async def``)
and the safe shapes that must NOT fire: an awaited alternative, a blocking
call in a nested sync ``def`` (executor-able), and one in a plain function.
"""

import subprocess
import sys
from pathlib import Path

from tools.lint.async_blocking_check import _dotted_name, _scan

_REPO = Path(__file__).resolve().parent.parent


class TestDottedName:
    def test_module_attr(self):
        import ast

        call = ast.parse("time.sleep(1)").body[0].value
        assert _dotted_name(call.func) == "time.sleep"

    def test_nested_attr(self):
        import ast

        call = ast.parse("urllib.request.urlopen(u)").body[0].value
        assert _dotted_name(call.func) == "urllib.request.urlopen"

    def test_bare_name_has_no_dotted_module(self):
        import ast

        call = ast.parse("sleep(1)").body[0].value
        assert _dotted_name(call.func) == "sleep"  # not in the blocking set


class TestScanFixtures:
    def _scan_src(self, tmp_path: Path, monkeypatch, src: str) -> dict[str, str]:
        f = tmp_path / "runtime" / "probe.py"
        f.parent.mkdir(parents=True)
        f.write_text(src, encoding="utf-8")
        import tools.lint.async_blocking_check as mod

        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(mod, "_SCAN_ROOTS", ("runtime",))
        return _scan()

    def test_flags_blocking_in_async(self, tmp_path, monkeypatch):
        hits = self._scan_src(
            tmp_path,
            monkeypatch,
            "import time\nasync def h():\n    time.sleep(1)\n",  # line 3
        )
        assert hits == {"runtime/probe.py:3": "time.sleep"}

    def test_awaited_alternative_is_clean(self, tmp_path, monkeypatch):
        hits = self._scan_src(
            tmp_path,
            monkeypatch,
            "import asyncio\nasync def h():\n    await asyncio.sleep(1)\n",
        )
        assert hits == {}

    def test_blocking_in_nested_sync_def_is_clean(self, tmp_path, monkeypatch):
        # The blocking work lives in a plain `def` that may be handed to an
        # executor — exactly the sanctioned pattern, must not fire.
        hits = self._scan_src(
            tmp_path,
            monkeypatch,
            "import time, asyncio\n"
            "async def h():\n"
            "    def _work():\n"
            "        time.sleep(1)\n"
            "    await asyncio.to_thread(_work)\n",
        )
        assert hits == {}

    def test_blocking_in_plain_function_is_clean(self, tmp_path, monkeypatch):
        hits = self._scan_src(
            tmp_path,
            monkeypatch,
            "import time\ndef h():\n    time.sleep(1)\n",
        )
        assert hits == {}


class TestRealTreeIsClean:
    def test_strict_passes_on_the_repo(self):
        result = subprocess.run(
            [sys.executable, "tools/lint/async_blocking_check.py", "--strict"],
            cwd=_REPO,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "OK" in result.stdout

