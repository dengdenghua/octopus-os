from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from runtime.execution.misc import office_pdf_preview


def test_office_pdf_preview_converts_once_and_reuses_cache(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "deck.pptx"
    source.write_bytes(b"pptx")
    cache = tmp_path / "cache"
    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []

    def fake_run(command: list[str], **_kwargs: Any) -> None:
        calls.append(command)
        environments.append(_kwargs["env"])
        output_dir = Path(command[command.index("--outdir") + 1])
        (output_dir / "deck.pdf").write_bytes(b"%PDF-1.7\npreview")

    monkeypatch.setattr(office_pdf_preview, "_CACHE_ROOT", cache)
    converter = tmp_path / "soffice"
    converter.write_text("#!/bin/sh\n")
    converter.chmod(0o755)
    monkeypatch.setattr(office_pdf_preview.shutil, "which", lambda _name: str(converter))
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-reach-converter")
    monkeypatch.setattr(office_pdf_preview.subprocess, "run", fake_run)

    first = office_pdf_preview.render_office_pdf(source)
    second = office_pdf_preview.render_office_pdf(source)

    assert first == second
    assert first is not None
    assert first.read_bytes().startswith(b"%PDF")
    assert len(calls) == 1
    assert len(environments) == 1
    assert "OPENAI_API_KEY" not in environments[0]
    assert any(
        argument.startswith("-env:UserInstallation=file:") for argument in calls[0]
    )
    assert Path(environments[0]["HOME"]).name == "profile"


def test_office_pdf_preview_falls_back_when_converter_fails(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "report.docx"
    source.write_bytes(b"docx")
    monkeypatch.setattr(office_pdf_preview, "_CACHE_ROOT", tmp_path / "cache")
    converter = tmp_path / "soffice"
    converter.write_text("#!/bin/sh\n")
    converter.chmod(0o755)
    monkeypatch.setattr(office_pdf_preview.shutil, "which", lambda _name: str(converter))

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise office_pdf_preview.subprocess.CalledProcessError(1, "soffice")

    monkeypatch.setattr(office_pdf_preview.subprocess, "run", fail)

    assert office_pdf_preview.render_office_pdf(source) is None


def test_office_pdf_preview_invalidates_cache_when_content_changes_with_same_metadata(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "deck.pptx"
    source.write_bytes(b"pptx")
    cache = tmp_path / "cache"
    calls: list[int] = []

    def fake_run(command: list[str], **_kwargs: Any) -> None:
        calls.append(len(calls) + 1)
        output_dir = Path(command[command.index("--outdir") + 1])
        (output_dir / "deck.pdf").write_bytes(f"%PDF call {len(calls)}".encode())

    monkeypatch.setattr(office_pdf_preview, "_CACHE_ROOT", cache)
    converter = tmp_path / "soffice"
    converter.write_text("#!/bin/sh\n")
    converter.chmod(0o755)
    monkeypatch.setattr(office_pdf_preview.shutil, "which", lambda _name: str(converter))
    monkeypatch.setattr(office_pdf_preview.subprocess, "run", fake_run)

    first_stat = source.stat()
    first = office_pdf_preview.render_office_pdf(source)
    source.write_bytes(b"ppty")
    os.utime(source, ns=(first_stat.st_atime_ns, first_stat.st_mtime_ns))
    second = office_pdf_preview.render_office_pdf(source)

    assert first is not None and second is not None
    assert first != second
    assert len(calls) == 2
