"""Implementation note."""

from __future__ import annotations

import json
from pathlib import Path

from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.notebook_skills import (
    _notebook_edit,
    _notebook_read,
    register_notebook_skills,
)


def _mk_nb(cells: list[dict]) -> dict:
    return {
        "cells": cells,
        "metadata": {"kernelspec": {"name": "python3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _write_nb(path: Path, cells: list[dict]) -> None:
    path.write_text(json.dumps(_mk_nb(cells)), encoding="utf-8")


# ─── Registration ────────────────────────────────────────────


class TestRegistration:
    def test_register_installs_both(self):
        r = SkillRegistry()
        count = register_notebook_skills(r)
        assert count == 2
        assert r.has("notebook_read")
        assert r.has("notebook_edit")


# ─── notebook_read ───────────────────────────────────────────


class TestNotebookRead:
    def test_reads_simple_notebook(self, tmp_path: Path):
        p = tmp_path / "x.ipynb"
        _write_nb(
            p,
            [
                {"cell_type": "markdown", "id": "m1", "source": "# Title\n", "metadata": {}},
                {
                    "cell_type": "code",
                    "id": "c1",
                    "source": "print(1)\n",
                    "metadata": {},
                    "outputs": [],
                    "execution_count": None,
                },
            ],
        )
        r = _notebook_read(path=str(p))
        assert r["cell_count"] == 2
        assert r["kernel"] == "python3"
        assert r["cells"][0]["cell_type"] == "markdown"
        assert r["cells"][0]["source"] == "# Title\n"

    def test_source_as_list_flattened_to_string(self, tmp_path: Path):
        # nbformat spec allows source as list of strings
        p = tmp_path / "x.ipynb"
        _write_nb(
            p,
            [
                {
                    "cell_type": "code",
                    "id": "c1",
                    "source": ["line1\n", "line2\n"],
                    "metadata": {},
                    "outputs": [],
                    "execution_count": 3,
                },
            ],
        )
        r = _notebook_read(path=str(p))
        assert r["cells"][0]["source"] == "line1\nline2\n"

    def test_code_outputs_text_preserved(self, tmp_path: Path):
        p = tmp_path / "x.ipynb"
        _write_nb(
            p,
            [
                {
                    "cell_type": "code",
                    "id": "c1",
                    "source": "print(42)",
                    "metadata": {},
                    "execution_count": 1,
                    "outputs": [
                        {"output_type": "stream", "name": "stdout", "text": "42\n"},
                    ],
                },
            ],
        )
        r = _notebook_read(path=str(p))
        assert r["cells"][0].get("output_text") == "42\n"

    def test_binary_output_dropped(self, tmp_path: Path):
        # Ensure large base64 image outputs don't pollute the LLM context
        p = tmp_path / "x.ipynb"
        _write_nb(
            p,
            [
                {
                    "cell_type": "code",
                    "id": "c1",
                    "source": "plot()",
                    "metadata": {},
                    "execution_count": 1,
                    "outputs": [
                        {"output_type": "display_data", "data": {"image/png": "AAAA" * 1000}},
                    ],
                },
            ],
        )
        r = _notebook_read(path=str(p))
        # No text/* mime → no output_text key, and the base64 blob must NOT
        # appear anywhere in the wire payload
        serialized = json.dumps(r)
        assert "AAAAAAAAAA" not in serialized

    def test_missing_file_returns_error(self):
        r = _notebook_read(path="/no/such/nb.ipynb")
        assert "error" in r

    def test_non_ipynb_extension(self, tmp_path: Path):
        p = tmp_path / "x.txt"
        p.write_text("{}")
        r = _notebook_read(path=str(p))
        assert "error" in r

    def test_invalid_json_returns_error(self, tmp_path: Path):
        p = tmp_path / "broken.ipynb"
        p.write_text("not json at all {{{")
        r = _notebook_read(path=str(p))
        assert "error" in r


# ─── notebook_edit ───────────────────────────────────────────


class TestNotebookEdit:
    def test_append_new_cell(self, tmp_path: Path):
        p = tmp_path / "x.ipynb"
        _write_nb(
            p,
            [
                {
                    "cell_type": "code",
                    "id": "c1",
                    "source": "a=1",
                    "metadata": {},
                    "outputs": [],
                    "execution_count": None,
                },
            ],
        )
        r = _notebook_edit(
            path=str(p),
            mode="append",
            new_source="# new section",
            cell_type="markdown",
        )
        assert r.get("ok")
        assert r["cell_count"] == 2
        # Verify persisted
        nb = json.loads(p.read_text(encoding="utf-8"))
        assert nb["cells"][-1]["cell_type"] == "markdown"
        assert nb["cells"][-1]["source"] == "# new section"

    def test_replace_by_index(self, tmp_path: Path):
        p = tmp_path / "x.ipynb"
        _write_nb(
            p,
            [
                {
                    "cell_type": "code",
                    "id": "c1",
                    "source": "old",
                    "metadata": {},
                    "outputs": [],
                    "execution_count": None,
                },
                {
                    "cell_type": "code",
                    "id": "c2",
                    "source": "keep",
                    "metadata": {},
                    "outputs": [],
                    "execution_count": None,
                },
            ],
        )
        r = _notebook_edit(
            path=str(p),
            mode="replace",
            cell_index=0,
            new_source="replaced",
            cell_type="code",
        )
        assert r.get("ok")
        nb = json.loads(p.read_text(encoding="utf-8"))
        assert nb["cells"][0]["source"] == "replaced"
        assert nb["cells"][1]["source"] == "keep"

    def test_replace_by_id(self, tmp_path: Path):
        p = tmp_path / "x.ipynb"
        _write_nb(
            p,
            [
                {
                    "cell_type": "code",
                    "id": "first",
                    "source": "a",
                    "metadata": {},
                    "outputs": [],
                    "execution_count": None,
                },
                {
                    "cell_type": "code",
                    "id": "target",
                    "source": "old",
                    "metadata": {},
                    "outputs": [],
                    "execution_count": None,
                },
            ],
        )
        r = _notebook_edit(
            path=str(p),
            mode="replace",
            cell_id="target",
            new_source="NEW",
            cell_type="code",
        )
        assert r.get("ok")
        nb = json.loads(p.read_text(encoding="utf-8"))
        assert nb["cells"][1]["source"] == "NEW"

    def test_insert_after_index(self, tmp_path: Path):
        p = tmp_path / "x.ipynb"
        _write_nb(
            p,
            [
                {
                    "cell_type": "code",
                    "id": "c1",
                    "source": "A",
                    "metadata": {},
                    "outputs": [],
                    "execution_count": None,
                },
                {
                    "cell_type": "code",
                    "id": "c2",
                    "source": "B",
                    "metadata": {},
                    "outputs": [],
                    "execution_count": None,
                },
            ],
        )
        _notebook_edit(
            path=str(p),
            mode="insert",
            cell_index=0,
            new_source="MID",
            cell_type="markdown",
        )
        nb = json.loads(p.read_text(encoding="utf-8"))
        assert [c["source"] for c in nb["cells"]] == ["A", "MID", "B"]

    def test_delete(self, tmp_path: Path):
        p = tmp_path / "x.ipynb"
        _write_nb(
            p,
            [
                {
                    "cell_type": "code",
                    "id": "c1",
                    "source": "A",
                    "metadata": {},
                    "outputs": [],
                    "execution_count": None,
                },
                {
                    "cell_type": "code",
                    "id": "c2",
                    "source": "B",
                    "metadata": {},
                    "outputs": [],
                    "execution_count": None,
                },
            ],
        )
        _notebook_edit(path=str(p), mode="delete", cell_index=0)
        nb = json.loads(p.read_text(encoding="utf-8"))
        assert [c["source"] for c in nb["cells"]] == ["B"]

    def test_missing_cell_returns_error(self, tmp_path: Path):
        p = tmp_path / "x.ipynb"
        _write_nb(
            p,
            [
                {
                    "cell_type": "code",
                    "id": "c1",
                    "source": "x",
                    "metadata": {},
                    "outputs": [],
                    "execution_count": None,
                },
            ],
        )
        r = _notebook_edit(
            path=str(p),
            mode="replace",
            cell_id="does_not_exist",
            new_source="y",
        )
        assert "error" in r

    def test_bad_mode_returns_error(self, tmp_path: Path):
        p = tmp_path / "x.ipynb"
        _write_nb(p, [])
        r = _notebook_edit(path=str(p), mode="nope")
        assert "error" in r

    def test_bad_cell_type_returns_error(self, tmp_path: Path):
        p = tmp_path / "x.ipynb"
        _write_nb(p, [])
        r = _notebook_edit(
            path=str(p),
            mode="append",
            new_source="x",
            cell_type="zzz",
        )
        assert "error" in r

    def test_atomic_write_no_tmp_leftover(self, tmp_path: Path):
        # After a successful edit, no .ipynb.tmp should remain next to the file
        p = tmp_path / "x.ipynb"
        _write_nb(
            p,
            [
                {
                    "cell_type": "code",
                    "id": "c1",
                    "source": "x",
                    "metadata": {},
                    "outputs": [],
                    "execution_count": None,
                },
            ],
        )
        _notebook_edit(
            path=str(p),
            mode="append",
            new_source="new",
            cell_type="code",
        )
        assert not (tmp_path / "x.ipynb.tmp").exists()
