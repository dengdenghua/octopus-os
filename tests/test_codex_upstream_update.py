from __future__ import annotations

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
