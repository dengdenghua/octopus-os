"""i18n module tests."""

from __future__ import annotations

from runtime.platform.i18n import L, _, get_lang, set_lang


class TestTranslation:
    """Test translation function."""

    def setup_method(self) -> None:
        # Reset to English before each test
        set_lang("en")

    def teardown_method(self) -> None:
        set_lang("en")

    # Implementation note.

    def test_en_translation(self) -> None:
        set_lang("en")
        assert _("cli.help.demo") == "End-to-end demo: list_cwd → read_file → count_words"

    def test_zh_translation(self) -> None:
        set_lang("zh")
        assert _("cli.help.demo") == "端到端演示：list_cwd → read_file → count_words"

    def test_default_is_en(self) -> None:
        set_lang("en")
        assert get_lang() == "en"

    def test_unknown_key_returns_key(self) -> None:
        assert _("nonexistent.key") == "nonexistent.key"

    # Implementation note.

    def test_interpolation_en(self) -> None:
        set_lang("en")
        result = _("cli.demo.done", steps=3, events=10, elapsed=1.234)
        assert "3" in result
        assert "10" in result
        assert "1.23" in result

    def test_interpolation_zh(self) -> None:
        set_lang("zh")
        result = _("cli.demo.done", steps=3, events=10, elapsed=1.234)
        assert "3" in result
        assert "10" in result
        assert "1.23" in result

    def test_interpolation_missing_vars(self) -> None:
        """Implementation note."""
        set_lang("en")
        # Implementation note.
        result = _("cli.demo.done")
        assert "{" in result

    # ── Fallback ────────────────────────────────────

    def test_fallback_to_en(self) -> None:
        """Implementation note."""
        set_lang("zh")
        # Implementation note.
        assert _("common.empty") == "(empty)"  # Implementation note.

    def test_zh_specific_key(self) -> None:
        set_lang("zh")
        assert _("common.success") == "成功"

    # ── Language switching ─────────────────────────

    def test_switch_lang(self) -> None:
        set_lang("en")
        assert _("cli.help.run") == "Run a custom goal"
        set_lang("zh")
        assert _("cli.help.run") == "运行自定义目标"
        set_lang("en")
        assert _("cli.help.run") == "Run a custom goal"

    # ── Lazy translation ──────────────────────────

    def test_lazy_str_evaluates_on_str(self) -> None:
        set_lang("en")
        lazy = L("cli.help.demo")
        assert isinstance(lazy, str) is False  # is LazyString
        assert str(lazy) == "End-to-end demo: list_cwd → read_file → count_words"

    def test_lazy_with_interpolation(self) -> None:
        set_lang("zh")
        lazy = L("cli.demo.done", steps=5, events=20, elapsed=2.5)
        text = str(lazy)
        assert "5" in text
        assert "20" in text

    def test_lazy_repr(self) -> None:
        lazy = L("cli.help.demo")
        assert "LazyString" in repr(lazy)

    # ── get_lang / set_lang ──────────────────────

    def test_get_lang_returns_current(self) -> None:
        set_lang("zh")
        assert get_lang() == "zh"
        set_lang("en")
        assert get_lang() == "en"

    def test_zh_cn_detected_as_zh(self) -> None:
        set_lang("zh_CN")
        # zh_CN should still resolve zh dict
        assert _("cli.help.demo") == "端到端演示：list_cwd → read_file → count_words"
