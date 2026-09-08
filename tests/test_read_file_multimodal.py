"""Tests for the multimodal `read_file` dispatcher in runtime.execution.suckers.builtins.

Covers extension-based dispatch to text / image / PDF / notebook handlers, plus the
PDF "must specify pages when >10 pages" refusal.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from runtime.execution.suckers import builtins as bm
from runtime.execution.suckers.builtins import _read_file

# 1x1 red PNG, hex-decoded so the test stays self-contained.
PNG_1x1_RED = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415478da6300fc0f0000020001f5d80a4c0000000049454e44ae426082"
)


class TestTextDispatch:
    def test_existing_text_behavior_preserved(self, tmp_path: Path):
        path = tmp_path / "a.txt"
        path.write_bytes(b"hello\nworld\n")
        r = _read_file(path=str(path))
        assert r["content"] == "hello\nworld\n"
        assert r["size"] == 12
        assert r["truncated"] is False
        # No "kind" set on the text path — it's the legacy shape.
        assert "kind" not in r


class TestImageDispatch:
    def test_png_returns_image_dict_with_base64(self, tmp_path: Path):
        path = tmp_path / "shot.png"
        path.write_bytes(PNG_1x1_RED)
        r = _read_file(path=str(path))
        assert r["ok"] is True
        assert r["kind"] == "image"
        assert r["media_type"] == "image/png"
        assert r["size_bytes"] == len(PNG_1x1_RED)
        # Round-trip the base64 to make sure we returned the original bytes intact.
        assert base64.standard_b64decode(r["data_base64"]) == PNG_1x1_RED
        assert r["path"].endswith("shot.png")

    @pytest.mark.parametrize(
        ("ext", "media_type"),
        [
            (".jpg", "image/jpeg"),
            (".jpeg", "image/jpeg"),
            (".webp", "image/webp"),
            (".gif", "image/gif"),
        ],
    )
    def test_other_image_extensions_route_to_image_handler(
        self,
        tmp_path: Path,
        ext: str,
        media_type: str,
    ):
        path = tmp_path / f"img{ext}"
        # The handler does no decode — bytes can be anything for the dispatch test.
        path.write_bytes(b"\x00\x01\x02fake")
        r = _read_file(path=str(path))
        assert r["kind"] == "image"
        assert r["media_type"] == media_type


def _make_pdf(path: Path, n_pages: int) -> None:
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as fh:
        writer.write(fh)


class TestPdfDispatch:
    def test_pdf_without_pages_and_more_than_ten_pages_refuses(self, tmp_path: Path):
        path = tmp_path / "big.pdf"
        _make_pdf(path, n_pages=25)
        r = _read_file(path=str(path))
        assert r["error"] == "pdf_too_large_without_pages"
        assert r["error_type"] == "invalid_argument"
        assert r["total_pages"] == 25
        assert "pages=" in r["hint"]

    def test_pdf_with_explicit_pages_returns_text(self, tmp_path: Path):
        # pdfplumber path is exercised when available; pypdf is an automatic fallback.
        pytest.importorskip("pypdf")
        path = tmp_path / "doc.pdf"
        _make_pdf(path, n_pages=4)
        r = _read_file(path=str(path), pages="1-2")
        assert r.get("ok") is True, r
        assert r["kind"] == "pdf"
        assert r["pages_extracted"] == [1, 2]
        assert r["total_pages"] == 4
        assert isinstance(r["text"], str)
        # Even with empty pages, header dividers should appear.
        assert "--- page 1 ---" in r["text"]
        assert "--- page 2 ---" in r["text"]

    def test_pdf_under_threshold_without_pages_reads_all(self, tmp_path: Path):
        pytest.importorskip("pypdf")
        path = tmp_path / "small.pdf"
        _make_pdf(path, n_pages=3)
        r = _read_file(path=str(path))
        assert r.get("ok") is True, r
        assert r["pages_extracted"] == [1, 2, 3]


class TestNotebookDispatch:
    def test_ipynb_delegates_to_notebook_read(self, tmp_path: Path, monkeypatch):
        path = tmp_path / "nb.ipynb"
        # Minimum viable notebook so the real handler doesn't bail before our spy fires.
        path.write_text(
            '{"cells": [], "nbformat": 4, "metadata": {"kernelspec": {"name": "python3"}}}',
            encoding="utf-8",
        )

        calls: list[dict] = []
        from runtime.execution.suckers import notebook_skills

        original = notebook_skills._notebook_read

        def spy(**kwargs):
            calls.append(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(notebook_skills, "_notebook_read", spy)
        r = _read_file(path=str(path))
        assert len(calls) == 1
        assert calls[0]["path"].endswith("nb.ipynb")
        assert r.get("cell_count") == 0
        assert r.get("cells") == []


class TestPagesParser:
    @pytest.mark.parametrize(
        ("spec", "total", "expected"),
        [
            ("1", 5, [1]),
            ("1-3", 5, [1, 2, 3]),
            ("1-3,7,10-12", 20, [1, 2, 3, 7, 10, 11, 12]),
            ("3,3,3", 5, [3]),  # dedup
        ],
    )
    def test_valid_specs(self, spec: str, total: int, expected: list[int]):
        pages, err = bm._parse_pages_spec(spec, total)
        assert err is None
        assert pages == expected

    @pytest.mark.parametrize(
        "spec",
        ["", "abc", "0", "5-3", "3-x", "100", "-1"],
    )
    def test_invalid_specs(self, spec: str):
        pages, err = bm._parse_pages_spec(spec, 10)
        assert pages is None
        assert err
