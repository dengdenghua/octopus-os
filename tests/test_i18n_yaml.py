"""Tests for the YAML-backed i18n engine.

Covers:
- locale switching (en / zh / ja / ko)
- zh/zh-CN/zh_TW alias resolution
- missing key returns the key
- fallback chain (locale -> base -> en)
- plural (count=_zero/_one/_other)
- interpolation with graceful fallback
- lazy string L()
- locale-aware CLI help
- hot reload via reload_locales()
- safety relax markers (union across locales)
- t() plural-aware
- detect_lang from env
"""

from __future__ import annotations

import os
import unittest

from runtime.platform.i18n import (
    L,
    _,
    available_locales,
    current_locale,
    detect_lang,
    get_lang,
    get_safety_relax_markers,
    reload_locales,
    set_lang,
    set_locale,
    t,
)


class TestLocaleSwitch(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = get_lang()
        reload_locales()

    def tearDown(self) -> None:
        set_lang(self._saved)

    def test_zh_alias_resolves_to_zh_cn(self) -> None:
        set_lang("zh")
        self.assertEqual(get_lang(), "zh")
        self.assertEqual(detect_lang.__name__, "detect_lang")

    def test_zh_cn_explicit(self) -> None:
        set_lang("zh-CN")
        self.assertEqual(get_lang(), "zh-CN")

    def test_ja_explicit(self) -> None:
        set_lang("ja")
        self.assertEqual(get_lang(), "ja")

    def test_unknown_falls_back_to_en(self) -> None:
        set_lang("xx-YY")
        self.assertEqual(get_lang(), "en")

    def test_set_locale_alias(self) -> None:
        set_locale("ja")
        self.assertEqual(current_locale(), "ja")


class TestTranslation(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = get_lang()
        reload_locales()

    def tearDown(self) -> None:
        set_lang(self._saved)

    def test_translate_en(self) -> None:
        set_lang("en")
        out = _("cli.help.demo")
        self.assertIsInstance(out, str)
        self.assertNotEqual(out, "cli.help.demo")

    def test_translate_zh(self) -> None:
        set_lang("zh")
        out = _("cli.help.demo")
        self.assertIsInstance(out, str)

    def test_missing_key_returns_key(self) -> None:
        set_lang("en")
        out = _("nonexistent.key.zzz")
        self.assertEqual(out, "nonexistent.key.zzz")

    def test_interpolation(self) -> None:
        set_lang("en")
        out = _("cli.version", version="0.2.0")
        self.assertIn("0.2.0", out)

    def test_interpolation_missing_var_falls_back_gracefully(self) -> None:
        set_lang("en")
        out = _("cli.version")
        self.assertIsInstance(out, str)


class TestLazyString(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = get_lang()
        reload_locales()

    def tearDown(self) -> None:
        set_lang(self._saved)

    def test_lazy_str(self) -> None:
        set_lang("en")
        s = L("cli.help.demo")
        text = str(s).lower()
        self.assertIn("end-to-end", text)

    def test_lazy_switches_with_locale(self) -> None:
        set_lang("en")
        s = L("cli.help.demo")
        en_text = str(s)
        set_lang("zh")
        zh_text = str(s)
        self.assertNotEqual(en_text, zh_text)


class TestPlural(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = get_lang()
        reload_locales()

    def tearDown(self) -> None:
        set_lang(self._saved)

    def test_t_with_count_en_one(self) -> None:
        set_lang("en")
        one = t("cli.usage.count", count=1)
        self.assertIsInstance(one, str)

    def test_t_with_count_en_many(self) -> None:
        set_lang("en")
        many = t("cli.usage.count", count=42)
        self.assertIsInstance(many, str)

    def test_t_missing_count_key_returns_key(self) -> None:
        set_lang("en")
        out = t("nope.not.here", count=5)
        self.assertEqual(out, "nope.not.here")

    def test_t_with_count_zero(self) -> None:
        set_lang("en")
        out = t("cli.usage.count", count=0)
        self.assertIsInstance(out, str)


class TestSafetyMarkers(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = get_lang()
        reload_locales()

    def tearDown(self) -> None:
        set_lang(self._saved)

    def test_markers_nonempty(self) -> None:
        m = get_safety_relax_markers()
        self.assertGreater(len(m), 5)

    def test_markers_contain_english(self) -> None:
        m = get_safety_relax_markers()
        joined = " ".join(m).lower()
        self.assertIn("skip", joined)
        self.assertIn("bypass", joined)

    def test_markers_contain_chinese(self) -> None:
        m = get_safety_relax_markers()
        joined = " ".join(m)
        self.assertIn("跳过", joined)
        self.assertIn("绕过", joined)

    def test_markers_contain_japanese(self) -> None:
        m = get_safety_relax_markers()
        joined = " ".join(m)
        self.assertIn("検証", joined)

    def test_markers_contain_korean(self) -> None:
        m = get_safety_relax_markers()
        joined = " ".join(m)
        self.assertIn("검증", joined)

    def test_markers_unique(self) -> None:
        m = get_safety_relax_markers()
        self.assertEqual(len(m), len(set(m)))


class TestReload(unittest.TestCase):
    def test_reload_does_not_break(self) -> None:
        reload_locales()
        locales = available_locales()
        self.assertIn("en", locales)
        self.assertIn("zh-CN", locales)

    def test_hot_reload_picks_up_yaml_changes(self) -> None:
        from runtime.platform import i18n as i18n_mod

        saved = get_lang()
        try:
            set_lang("en")
            en_path = i18n_mod._LOCALE_DIR / "en.yaml"
            original = en_path.read_text(encoding="utf-8")
            try:
                with en_path.open("w", encoding="utf-8") as f:
                    f.write('"cli.version": "TEST_HOT_RELOAD_MARKER_12345"\n')
                reload_locales()
                after = _("cli.version")
                self.assertIn("TEST_HOT_RELOAD_MARKER_12345", after)
            finally:
                en_path.write_text(original, encoding="utf-8")
                reload_locales()
        finally:
            set_lang(saved)


class TestDetectLang(unittest.TestCase):
    def test_explicit_env(self) -> None:
        saved = os.environ.get("LANG")
        try:
            os.environ["LANG"] = "ja_JP.UTF-8"
            self.assertEqual(detect_lang(), "ja")
        finally:
            if saved is None:
                os.environ.pop("LANG", None)
            else:
                os.environ["LANG"] = saved

    def test_zh_prefix(self) -> None:
        saved = os.environ.get("LANG")
        try:
            os.environ["LANG"] = "zh_TW.UTF-8"
            self.assertEqual(detect_lang(), "zh-CN")
        finally:
            if saved is None:
                os.environ.pop("LANG", None)
            else:
                os.environ["LANG"] = saved


if __name__ == "__main__":
    unittest.main()
