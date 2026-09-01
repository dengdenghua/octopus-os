from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.execution.misc import office_fidelity_preview


def test_fidelity_preview_embeds_rendered_pages_and_reuses_cache(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "deck.pptx"
    source.write_bytes(b"pptx")
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF")
    converter = tmp_path / "pdftoppm"
    converter.write_text("#!/bin/sh\n")
    converter.chmod(0o755)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: Any) -> None:
        calls.append(command)
        prefix = Path(command[-1])
        prefix.with_name(f"{prefix.name}-1.jpg").write_bytes(b"jpeg-page-one")
        prefix.with_name(f"{prefix.name}-2.jpg").write_bytes(b"jpeg-page-two")

    monkeypatch.setattr(office_fidelity_preview, "_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(office_fidelity_preview, "_find_qlmanage", lambda: None)
    monkeypatch.setattr(office_fidelity_preview, "render_office_pdf", lambda _path: pdf)
    monkeypatch.setattr(office_fidelity_preview.shutil, "which", lambda _name: str(converter))
    monkeypatch.setattr(office_fidelity_preview.subprocess, "run", fake_run)

    first = office_fidelity_preview.render_office_fidelity_preview(source)
    second = office_fidelity_preview.render_office_fidelity_preview(source)

    assert first == second
    assert first is not None
    assert first.count("data:image/jpeg;base64,") == 2
    assert "deck.pptx · 2 pages" in first
    assert len(calls) == 1


def test_fidelity_preview_prefers_quicklook_and_sanitizes_html(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "deck.pptx"
    source.write_bytes(b"pptx")
    qlmanage = tmp_path / "qlmanage"
    pdftoppm = tmp_path / "pdftoppm"
    for executable in (qlmanage, pdftoppm):
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o755)
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: Any) -> None:
        commands.append(command)
        if command[0] == str(qlmanage):
            preview = Path(command[3]) / "deck.pptx.qlpreview"
            preview.mkdir()
            (preview / "Attachment1.pdf").write_bytes(b"%PDF")
            (preview / "Attachment2.css").write_text(
                ".sheet{font-size:11;width: 612;}", encoding="utf-8"
            )
            (preview / "Preview.html").write_text(
                '<html><head><meta name="viewport" content="width=612">'
                '<link href="Attachment2.css" rel="stylesheet">'
                '<style>.slide{width:960;height:540;color:red}</style></head><body>'
                '<div class="slide">设计规避分析<img src="Attachment1.pdf"></div>'
                '<script>alert("unsafe")</script></body></html>',
                encoding="utf-8",
            )
        else:
            Path(f"{command[-1]}.png").write_bytes(b"png-page")

    monkeypatch.setattr(office_fidelity_preview, "_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(office_fidelity_preview, "_find_qlmanage", lambda: str(qlmanage))
    monkeypatch.setattr(office_fidelity_preview, "_find_pdftoppm", lambda: str(pdftoppm))
    monkeypatch.setattr(office_fidelity_preview.subprocess, "run", fake_run)
    monkeypatch.setattr(
        office_fidelity_preview,
        "render_office_pdf",
        lambda _path: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )

    html = office_fidelity_preview.render_office_fidelity_preview(source)

    assert html is not None
    assert "设计规避分析" in html
    assert "data:image/png;base64," in html
    assert "Attachment1.pdf" not in html
    assert "<script" not in html
    assert "Content-Security-Policy" in html
    assert "width:960px;height:540px" in html
    assert "font-size:11px;width:612px" in html
    assert "Attachment2.css" in html
    assert '<link href="Attachment2.css"' not in html
    assert "zoom:min(1,calc(100vw/612px))" in html
    assert "scroll-snap-type:y mandatory" in html
    assert "body>div.slide" in html
    assert "scroll-snap-stop:always" in html
    assert len(commands) == 2


def test_fidelity_preview_falls_back_when_page_renderer_is_missing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF")
    monkeypatch.setattr(office_fidelity_preview, "_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(office_fidelity_preview, "_find_pdftoppm", lambda: None)

    assert office_fidelity_preview.render_office_fidelity_preview(source) is None

