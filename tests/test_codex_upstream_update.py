from __future__ import annotations

import asyncio
from contextlib import suppress

from runtime.execution.codex_backend.upstream_update import (
    CodexUpstreamUpdateService,
    resolve_bundled_codex_version,
)


def _metadata(version: str = "0.150.0") -> dict[str, object]:
    return {
        "version": version,
        "dist": {
            "integrity": "sha512-approved",
            "tarball": f"https://registry.npmjs.org/codex/-/codex-{version}.tgz",
        },
    }


def test_resolves_version_from_packaged_executable_bundle(monkeypatch, tmp_path):
    codex_root = tmp_path / "codex"
    executable = codex_root / "bin" / "codex"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"native-codex")
    (codex_root / "echo-codex-bundle.json").write_text(
        '{"schema":"echo.codex_bundle.v1","package":"@openai/codex","version":"0.148.1"}',
        encoding="utf-8",
    )
    monkeypatch.delenv("ECHO_PACKAGED_CODEX_VERSION", raising=False)
    monkeypatch.setenv("ECHO_CODEX_EXECUTABLE", str(executable))

    assert resolve_bundled_codex_version() == "0.148.1"


def test_rejects_untrusted_packaged_manifest_identity(monkeypatch, tmp_path):
    codex_root = tmp_path / "codex"
    executable = codex_root / "bin" / "codex"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"native-codex")
    (codex_root / "echo-codex-bundle.json").write_text(
        '{"schema":"untrusted","package":"@openai/codex","version":"99.0.0"}',
        encoding="utf-8",
    )
    monkeypatch.delenv("ECHO_PACKAGED_CODEX_VERSION", raising=False)
    monkeypatch.setenv("ECHO_CODEX_EXECUTABLE", str(executable))

    assert resolve_bundled_codex_version() != "99.0.0"


def test_detects_and_persists_new_codex_release(tmp_path):
    service = CodexUpstreamUpdateService(
        tmp_path / "status.json",
        current_version="0.149.0",
        fetcher=lambda _url, _timeout: _metadata(),
    )

    status = service.check()

    assert status.update_available is True
    assert status.latest_version == "0.150.0"
    assert status.approval_status == "pending"
    assert status.integrity == "sha512-approved"
    assert service.read() == status


def test_approval_only_marks_candidate_for_next_echo_release(tmp_path):
    service = CodexUpstreamUpdateService(
        tmp_path / "status.json",
        current_version="0.149.0",
        fetcher=lambda _url, _timeout: _metadata(),
    )
    service.check()

    approved = service.approve("0.150.0")

    assert approved.approval_status == "approved_for_next_release"
    assert approved.approved_version == "0.150.0"
    assert approved.approved_at
    assert service.read().tarball_url.endswith("codex-0.150.0.tgz")


def test_rejects_stale_or_unknown_approval(tmp_path):
    service = CodexUpstreamUpdateService(
        tmp_path / "status.json",
        current_version="0.149.0",
        fetcher=lambda _url, _timeout: _metadata(),
    )
    service.check()

    try:
        service.approve("0.151.0")
    except ValueError as exc:
        assert "not current" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("stale candidate approval must fail")


def test_network_failure_preserves_last_good_candidate(tmp_path):
    calls = 0

    def fetch(_url: str, _timeout: float):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _metadata()
        raise TimeoutError("upstream timed out")

    service = CodexUpstreamUpdateService(
        tmp_path / "status.json",
        current_version="0.149.0",
        fetcher=fetch,
    )
    service.check()

    failed = service.check()

    assert failed.latest_version == "0.150.0"
    assert failed.update_available is True
    assert failed.error == "upstream timed out"


def test_rejects_unverified_or_insecure_metadata(tmp_path):
    service = CodexUpstreamUpdateService(
        tmp_path / "status.json",
        current_version="0.149.0",
        fetcher=lambda _url, _timeout: {
            "version": "0.150.0",
            "dist": {"tarball": "http://example.test/codex.tgz"},
        },
    )

    status = service.check()

    assert status.update_available is False
    assert status.error == "Codex package integrity is missing"


def test_closed_service_stops_writing_status_file(tmp_path):
    state = tmp_path / "status.json"
    service = CodexUpstreamUpdateService(
        state,
        current_version="0.149.0",
        fetcher=lambda _url, _timeout: _metadata(),
    )
    service.check()
    before = state.read_text(encoding="utf-8")

    # close() flips this flag *before* cancelling the worker task. Without it a
    # check already running on the thread keeps writing while the app tears
    # down, and any failure on that path escapes through lifespan teardown.
    service._closed = True
    service.check()

    assert state.read_text(encoding="utf-8") == before


def test_write_surfaces_real_failure_when_cleanup_also_fails(tmp_path, monkeypatch):
    from runtime.execution.codex_backend import upstream_update as mod

    service = mod.CodexUpstreamUpdateService(
        tmp_path / "status.json",
        current_version="0.149.0",
        fetcher=lambda _url, _timeout: _metadata(),
    )
    status = service.check()

    def boom_replace(_src, _dst):
        raise OSError("replace failed")

    def boom_unlink(_self, missing_ok=False):
        raise OSError("cleanup failed")

    monkeypatch.setattr(mod.os, "replace", boom_replace)
    monkeypatch.setattr(mod.Path, "unlink", boom_unlink)

    try:
        service._write(status)
    except OSError as exc:
        assert "replace failed" in str(exc), "cleanup failure masked the real error"
    else:  # pragma: no cover
        raise AssertionError("_write must surface the real persistence failure")


def test_run_survives_repeated_check_failures(tmp_path):
    def boom(_url, _timeout):
        raise RuntimeError("radar boom")

    service = CodexUpstreamUpdateService(
        tmp_path / "status.json",
        current_version="0.149.0",
        fetcher=boom,
        initial_check_delay_seconds=0,
        check_interval_seconds=0,
    )

    async def scenario():
        task = asyncio.create_task(service._run())
        await asyncio.sleep(0.05)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return task

    task = asyncio.run(scenario())

    # The radar must still be alive when we cancel it. An unhandled check
    # failure would end the loop early, leaving the task finished with an
    # exception instead of cancelled.
    assert task.cancelled()
