"""Exercise ``_LSPClient`` against a real subprocess.

Every other test in ``test_lsp_skills.py`` substitutes ``_FakeClient``, which
returns whatever the test fed it. That covers the translation layer and
nothing about the transport: framing, thread handoff, a server that wedges,
a server that dies mid-request, a server that talks too much on stderr. Those
only fail when a process is actually on the other end, which is why the
reference-seeding bug survived a green suite -- a fake client answers about
files it was never asked to open.

The server here is scripted (``tests/lsp_fake_server.py``), so these stay
hermetic and fast; the end-to-end tests against a genuine language server are
marked ``integration`` at the bottom.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from runtime.execution.suckers import lsp_skills
from runtime.execution.suckers._lsp_candidates import candidate_files, identifier_at
from runtime.execution.suckers.lsp_skills import (
    _LSPClient,
    _LSPError,
    _LSPRequestError,
    _LSPTimeoutError,
    _LSPTransportError,
)

ROOT = Path(__file__).resolve().parents[1]


def _argv(behaviour: str) -> list[str]:
    return [sys.executable, "-m", "tests.lsp_fake_server", behaviour]


@pytest.fixture
def client(request: pytest.FixtureRequest, tmp_path: Path):
    """A started client against the scripted server, always shut down."""
    behaviour = getattr(request, "param", "normal")
    c = _LSPClient("python")
    try:
        c.start(_argv(behaviour), str(tmp_path))
    except _LSPError:
        c.shutdown()
        raise
    yield c
    c.shutdown()


def _sample(tmp_path: Path, name: str = "m.py", body: str = "def target():\n    pass\n") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# ── transport ────────────────────────────────────────────────────────────


def test_handshake_completes_against_a_real_process(client: _LSPClient) -> None:
    assert client.is_alive()


def test_request_and_response_are_correlated(client: _LSPClient, tmp_path: Path) -> None:
    path = _sample(tmp_path)
    client.ensure_open(str(path))
    result = client.request(
        "textDocument/definition",
        {
            "textDocument": {"uri": lsp_skills._path_to_uri(str(path))},
            "position": {"line": 0, "character": 4},
        },
    )
    assert result


def test_concurrent_requests_do_not_cross_deliver(client: _LSPClient, tmp_path: Path) -> None:
    """Two in-flight methods must each get their own reply, not the other's.

    A reader thread that dispatches by arrival order rather than by id passes
    every single-request test and corrupts every concurrent one.
    """
    path = _sample(tmp_path)
    client.ensure_open(str(path))
    uri = lsp_skills._path_to_uri(str(path))
    params = {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 4}}
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def call(method: str, key: str) -> None:
        try:
            results[key] = client.request(method, dict(params))
        except BaseException as exc:  # noqa: BLE001 — recorded for the assert
            errors.append(exc)

    threads = [
        threading.Thread(target=call, args=("textDocument/hover", "hover")),
        threading.Thread(target=call, args=("textDocument/documentSymbol", "symbols")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    # documentSymbol answers with a list, hover with a contents mapping. If
    # the replies were swapped, the shapes would be too.
    assert isinstance(results["symbols"], list)
    assert isinstance(results["hover"], dict) and "contents" in results["hover"]


@pytest.mark.parametrize("client", ["hang"], indirect=True)
def test_a_wedged_server_times_out_instead_of_hanging(client: _LSPClient) -> None:
    """An untimed wait here would hang the agent's turn, not just this call."""
    started = time.monotonic()
    with pytest.raises(_LSPTimeoutError):
        client.request("textDocument/definition", {}, timeout=1.0)
    assert time.monotonic() - started < 5.0


@pytest.mark.parametrize("client", ["crash"], indirect=True)
def test_a_dying_server_does_not_leave_a_caller_waiting_forever(client: _LSPClient) -> None:
    with pytest.raises(_LSPError):
        client.request("textDocument/definition", {}, timeout=3.0)


@pytest.mark.parametrize("client", ["error"], indirect=True)
def test_an_error_response_raises_rather_than_returning_empty(client: _LSPClient) -> None:
    """Silently returning {} would read as 'no definition found'."""
    with pytest.raises(_LSPRequestError):
        client.request("textDocument/definition", {}, timeout=5.0)


@pytest.mark.parametrize("client", ["noisy"], indirect=True)
def test_a_verbose_server_does_not_deadlock_on_its_stderr(
    client: _LSPClient, tmp_path: Path
) -> None:
    """stderr is a pipe: unread, the server blocks writing and we wait forever.

    The scripted server writes well past a pipe buffer before replying.
    """
    path = _sample(tmp_path)
    client.ensure_open(str(path))
    result = client.request(
        "textDocument/definition",
        {
            "textDocument": {"uri": lsp_skills._path_to_uri(str(path))},
            "position": {"line": 0, "character": 4},
        },
        timeout=15.0,
    )
    assert result
    assert client.stderr_tail()


def test_startup_failure_reports_the_reason_from_stderr(tmp_path: Path) -> None:
    """A binary that runs but refuses to serve must say why.

    ``rust-analyzer`` installed as a rustup shim resolves on PATH, exits 1,
    and explains itself only on stderr. Without it the caller is told the
    connection closed, which points at the wrong problem entirely.
    """
    c = _LSPClient("python")
    argv = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('boom: no toolchain\\n'); sys.exit(3)",
    ]
    with pytest.raises(_LSPTransportError) as excinfo:
        c.start(argv, str(tmp_path))
    c.shutdown()
    message = str(excinfo.value)
    assert "boom: no toolchain" in message
    assert "exit=3" in message


def test_spawn_failure_names_the_missing_binary(tmp_path: Path) -> None:
    c = _LSPClient("python")
    with pytest.raises(_LSPTransportError, match="no-such-language-server"):
        c.start(["no-such-language-server-xyz"], str(tmp_path))
    c.shutdown()


def test_start_fails_when_initialize_is_never_answered(tmp_path: Path) -> None:
    c = _LSPClient("python")
    c.INIT_TIMEOUT = 2.0
    with pytest.raises(_LSPError):
        c.start(_argv("no_initialize_reply"), str(tmp_path))
    c.shutdown()


def test_shutdown_is_idempotent(client: _LSPClient) -> None:
    client.shutdown()
    client.shutdown()
    assert not client.is_alive()


def test_request_after_shutdown_is_refused(client: _LSPClient) -> None:
    client.shutdown()
    with pytest.raises(_LSPTransportError):
        client.request("textDocument/definition", {}, timeout=1.0)


def test_uri_roundtrip_survives_awkward_paths(tmp_path: Path) -> None:
    for name in ("plain.py", "with space.py", "unicode-查找.py", "hash#tag.py"):
        path = tmp_path / name
        assert lsp_skills._uri_to_path(lsp_skills._path_to_uri(str(path))) == str(path)


# ── candidate seeding ────────────────────────────────────────────────────


def test_identifier_at_reads_the_symbol_under_a_one_based_position(tmp_path: Path) -> None:
    path = _sample(tmp_path, body="def check_path(value):\n    return value\n")
    assert identifier_at(path, 1, 5) == "check_path"
    assert identifier_at(path, 1, 14) == "check_path"
    assert identifier_at(path, 1, 4) is None  # the space before the name
    assert identifier_at(path, 99, 1) is None  # past EOF


def test_candidate_files_finds_mentions_and_skips_the_rest(tmp_path: Path) -> None:
    (tmp_path / "caller.py").write_text("from d import target\ntarget()\n", encoding="utf-8")
    (tmp_path / "d.py").write_text("def target():\n    pass\n", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("def other():\n    pass\n", encoding="utf-8")
    found, truncated = candidate_files("target", tmp_path, frozenset({".py"}))
    names = {p.name for p in found}
    assert {"caller.py", "d.py"} <= names
    assert "unrelated.py" not in names
    assert truncated is False


def test_a_capped_candidate_search_says_it_was_capped(tmp_path: Path) -> None:
    """A truncated search that looks complete is worse than a slow one."""
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("target()\n", encoding="utf-8")
    found, truncated = candidate_files("target", tmp_path, frozenset({".py"}), limit=2)
    assert len(found) == 2
    assert truncated is True


def test_candidate_search_rejects_a_non_identifier() -> None:
    """The name reaches a subprocess argv, so only identifiers may pass."""
    found, _ = candidate_files("; rm -rf /", ROOT, frozenset({".py"}))
    assert found == []


def test_seeding_opens_callers_before_the_reference_query(tmp_path: Path) -> None:
    """The regression test for the bug: the caller must be opened too.

    ``_lsp_references`` used to open only the file holding the definition,
    so a server that answers from open documents reported zero references
    for a symbol used across the project -- with ``ok: True``.
    """
    definition = tmp_path / "d.py"
    definition.write_text("def target():\n    pass\n", encoding="utf-8")
    caller = tmp_path / "caller.py"
    caller.write_text("from d import target\ntarget()\n", encoding="utf-8")

    opened: list[str] = []

    class _Recorder:
        language = "python"

        def ensure_open(self, path: str) -> None:
            opened.append(path)

    seeded, truncated = lsp_skills._seed_reference_candidates(
        _Recorder(), definition, tmp_path, 1, 5
    )
    assert seeded == 1
    assert truncated is False
    assert [Path(p).name for p in opened] == ["caller.py"]


def test_seeding_survives_an_unreadable_candidate(tmp_path: Path) -> None:
    """One bad file must not sink the whole query."""
    definition = tmp_path / "d.py"
    definition.write_text("def target():\n    pass\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("target()\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("target()\n", encoding="utf-8")

    class _Flaky:
        language = "python"
        seen = 0

        def ensure_open(self, path: str) -> None:
            self.seen += 1
            if self.seen == 1:
                raise _LSPTransportError("read_failed")

    seeded, _ = lsp_skills._seed_reference_candidates(_Flaky(), definition, tmp_path, 1, 5)
    assert seeded == 1


def test_seeding_is_skipped_when_no_identifier_is_under_the_cursor(tmp_path: Path) -> None:
    definition = tmp_path / "d.py"
    definition.write_text("x = 1\n", encoding="utf-8")

    class _Boom:
        language = "python"

        def ensure_open(self, path: str) -> None:  # pragma: no cover - must not run
            raise AssertionError("should not open anything")

    assert lsp_skills._seed_reference_candidates(_Boom(), definition, tmp_path, 1, 2) == (0, False)


# ── server resolution ────────────────────────────────────────────────────


def test_an_uninstalled_module_candidate_does_not_shadow_the_next_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ``-m`` candidate must be skipped when its module is absent.

    Treating ``sys.executable`` as always-runnable selected the pylsp
    candidate whether or not pylsp was installed, and being selected it
    shadowed the pyright candidate behind it: the server spawned, died on
    ``No module named pylsp``, and the installed alternative was reported
    missing.
    """
    installed = tmp_path / "pyright-langserver"
    installed.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    installed.chmod(0o755)
    monkeypatch.setattr(lsp_skills, "_local_bin_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(lsp_skills, "_module_importable", lambda module: False)
    monkeypatch.setattr(
        lsp_skills,
        "_SERVER_CANDIDATES",
        {
            "python": [
                [sys.executable, "-m", "definitely_not_installed"],
                ["pyright-langserver", "--stdio"],
            ]
        },
    )
    argv = lsp_skills._resolve_server_argv("python")
    assert argv is not None
    assert argv[0] == str(installed)
    assert argv[1:] == ["--stdio"]


def test_an_installed_module_candidate_is_still_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lsp_skills, "_module_importable", lambda module: True)
    monkeypatch.setattr(
        lsp_skills, "_SERVER_CANDIDATES", {"python": [[sys.executable, "-m", "json"]]}
    )
    assert lsp_skills._resolve_server_argv("python") == [sys.executable, "-m", "json"]


def test_interpreter_adjacent_bin_wins_over_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A server pinned beside the interpreter matches the project's deps.

    On this repo it is also not on PATH unless the venv was activated, which
    made every Python LSP skill report "no server installed".
    """
    local = tmp_path / "local"
    local.mkdir()
    pinned = local / "pyright-langserver"
    pinned.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pinned.chmod(0o755)
    monkeypatch.setattr(lsp_skills, "_local_bin_dirs", lambda: [str(local)])
    monkeypatch.setattr(lsp_skills.shutil, "which", lambda exe: "/usr/bin/pyright-langserver")
    monkeypatch.setattr(
        lsp_skills, "_SERVER_CANDIDATES", {"python": [["pyright-langserver", "--stdio"]]}
    )
    assert lsp_skills._resolve_server_argv("python") == [str(pinned), "--stdio"]


def test_resolution_falls_back_to_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(lsp_skills, "_local_bin_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(lsp_skills.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    monkeypatch.setattr(lsp_skills, "_SERVER_CANDIDATES", {"go": [["gopls"]]})
    assert lsp_skills._resolve_server_argv("go") == ["/usr/bin/gopls"]


def test_no_runnable_candidate_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(lsp_skills, "_local_bin_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(lsp_skills.shutil, "which", lambda exe: None)
    monkeypatch.setattr(lsp_skills, "_module_importable", lambda module: False)
    assert lsp_skills._resolve_server_argv("python") is None


# ── end to end, needs a genuine language server ──────────────────────────


def _pyright_available() -> bool:
    exe = ROOT / ".venv" / "bin" / "pyright-langserver"
    return exe.exists()


@pytest.mark.integration
@pytest.mark.skipif(not _pyright_available(), reason="pyright-langserver not in .venv")
def test_references_finds_cross_file_uses_against_real_pyright(tmp_path: Path) -> None:
    """The bug, reproduced and fixed against the server that exhibits it.

    pyright answers ``textDocument/references`` only from open documents, so
    without seeding this returns 1 (the declaration) instead of 3.
    """
    (tmp_path / "pyproject.toml").write_text("[project]\nname='t'\nversion='0'\n", encoding="utf-8")
    (tmp_path / "d.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("from d import target\n\nprint(target())\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from d import target\n\nprint(target())\n", encoding="utf-8")

    env_path = f"{ROOT / '.venv' / 'bin'}:{subprocess.os.environ.get('PATH', '')}"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PATH", env_path)
        lsp_skills._reset_clients_for_test()
        try:
            result = lsp_skills._lsp_references(
                path=str(tmp_path / "d.py"), line=1, column=5, sandbox_dir=str(tmp_path)
            )
        finally:
            lsp_skills._reset_clients_for_test()

    assert result["ok"] is True, result
    assert result["count"] >= 3, result
    paths = {Path(ref["path"]).name for ref in result["references"]}
    assert {"a.py", "b.py"} <= paths
    assert result["searched_files"] >= 2

