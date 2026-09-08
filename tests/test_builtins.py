"""Implementation note."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import runtime.execution.suckers.builtins as builtins_module
from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.builtins import (
    BUILTIN_NAMES,
    _count_words,
    _file_stats,
    _hash_text,
    _list_cwd,
    _read_file,
    register_builtins,
)


class TestRegistration:
    def test_register_all_returns_registry(self):
        r = SkillRegistry()
        register_builtins(r)
        for name in BUILTIN_NAMES:
            assert r.has(name), f"missing: {name}"

    def test_all_trusted_sources_are_skill_public(self):
        r = SkillRegistry()
        register_builtins(r)
        for name in BUILTIN_NAMES:
            assert r.get(name).trusted_source.startswith("skill://public/")


class TestListCwd:
    def test_lists_files(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("A")
        (tmp_path / "sub").mkdir()
        result = _list_cwd(path=str(tmp_path))
        assert result["count"] == 2
        names = {item["name"] for item in result["items"]}
        assert {"a.txt", "sub"} <= names

    def test_missing_path_returns_error(self):
        result = _list_cwd(path="/nonexistent/xxx/zzz")
        assert "error" in result

    def test_hidden_files_excluded(self, tmp_path: Path):
        (tmp_path / "visible.txt").write_text("ok")
        (tmp_path / ".hidden").write_text("no")
        result = _list_cwd(path=str(tmp_path))
        names = {item["name"] for item in result["items"]}
        assert "visible.txt" in names
        assert ".hidden" not in names


class TestReadFile:
    def test_reads_utf8(self, tmp_path: Path):
        path = tmp_path / "a.txt"
        # Implementation note.
        path.write_bytes(b"hello\nworld\n")
        r = _read_file(path=str(path))
        assert r["content"] == "hello\nworld\n"
        assert r["size"] == 12
        assert r["truncated"] is False

    def test_reads_requested_range(self, tmp_path: Path):
        path = tmp_path / "range.txt"
        path.write_bytes(b"line0\nline1\nline2\nline3\n")
        r = _read_file(path=str(path), offset=2, limit=2)
        assert r["content"] == "line2\nline3\n"
        assert r["truncated"] is False

    def test_truncates_large(self, tmp_path: Path):
        path = tmp_path / "big.txt"
        path.write_text("x" * 200_000, encoding="utf-8")
        r = _read_file(path=str(path), max_bytes=1000)
        assert r["truncated"] is True
        assert len(r["content"]) == 1000

    def test_ignores_tail_only_when_range_is_set(self, tmp_path: Path):
        path = tmp_path / "tail.txt"
        path.write_bytes(b"a\nb\nc\nd\n")
        r = _read_file(path=str(path), offset=1, limit=1, max_bytes=2)
        assert r["content"] == "b\n"
        assert r["lines_read"] == 1

    def test_missing_file(self):
        r = _read_file(path="/nope/nope/nope")
        assert "error" in r

    def test_reads_structured_document_in_isolated_worker(self, tmp_path: Path):
        path = tmp_path / "brief.docx"
        data = BytesIO()
        with ZipFile(data, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="urn:w"><w:body><w:p><w:r><w:t>'
                "Worker isolated brief"
                "</w:t></w:r></w:p></w:body></w:document>",
            )
        path.write_bytes(data.getvalue())

        result = _read_file(path=str(path))

        assert result["ok"] is True
        assert result["kind"] == "document"
        assert result["text"] == "Worker isolated brief"

    def test_structured_document_worker_failure_does_not_fallback_to_parser(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        path = tmp_path / "brief.docx"
        path.write_bytes(b"not a real docx")
        monkeypatch.setattr(
            builtins_module,
            "extract_document_isolated",
            lambda *_args, **_kwargs: {"outcome": "worker_failed", "text": None},
        )

        result = _read_file(path=str(path))

        assert result["error_type"] == "worker_failed"
        assert "document_parse_failed" in result["error"]

    def test_reads_notebook_in_isolated_worker(self, tmp_path: Path):
        path = tmp_path / "analysis.ipynb"
        path.write_text(
            json.dumps(
                {
                    "cells": [
                        {"cell_type": "markdown", "source": "# Worker notebook"},
                    ],
                    "metadata": {"kernelspec": {"name": "python3"}},
                    "nbformat": 4,
                }
            ),
            encoding="utf-8",
        )
        result = _read_file(path=str(path))
        assert result["cell_count"] == 1
        assert result["kernel"] == "python3"
        assert result["cells"][0]["source"] == "# Worker notebook"


class TestCountWords:
    def test_basic(self):
        r = _count_words(text="hello world\nfoo bar baz")
        assert r["chars"] == 23
        assert r["words"] == 5
        assert r["lines"] == 2

    def test_empty(self):
        r = _count_words(text="")
        assert r == {"chars": 0, "words": 0, "lines": 0}


class TestHashText:
    def test_blake2b_default(self):
        r = _hash_text(text="abc")
        assert r["algorithm"] == "blake2b"
        assert len(r["hash"]) == 32  # digest_size=16 → 32 hex chars

    def test_sha256(self):
        r = _hash_text(text="abc", algorithm="sha256")
        assert r["algorithm"] == "sha256"
        assert len(r["hash"]) == 64

    def test_unknown_algo(self):
        r = _hash_text(text="x", algorithm="md42")
        assert "error" in r


class TestFileStats:
    def test_file(self, tmp_path: Path):
        p = tmp_path / "x.txt"
        p.write_text("hello")
        r = _file_stats(path=str(p))
        assert r["size"] == 5
        assert r["is_file"] is True
        assert r["is_dir"] is False

    def test_missing(self):
        r = _file_stats(path="/missing")
        assert "error" in r
