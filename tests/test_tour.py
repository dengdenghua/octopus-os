"""Implementation note."""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from runtime.tour import CHAPTERS, run_tour


@pytest.fixture(autouse=True)
def zh_locale():
    """Implementation note."""
    from runtime.platform.i18n import get_lang, set_lang

    prior = get_lang()
    set_lang("zh")
    yield
    set_lang(prior)


class TestTour:
    def test_all_chapters_registered(self):
        """Implementation note."""
        assert len(CHAPTERS) == 10
        # Implementation note.
        for title, fn in CHAPTERS:
            assert isinstance(title, str) and title
            assert callable(fn)

    def test_run_tour_without_pause_exit_0(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_tour(pause=False, color=False)
        assert rc == 0
        out = buf.getvalue()
        # Implementation note.
        assert out.count("结论：") == 10

    def test_no_chapter_fails(self):
        """Implementation note."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            run_tour(pause=False, color=False)
        assert "章节失败" not in buf.getvalue()

    def test_chapter_limit(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_tour(chapters=3, pause=False, color=False)
        assert rc == 0
        assert buf.getvalue().count("结论：") == 3
