"""Internationalization for Echo Agent.

Public API (backward compatible):
    _    (key, **kwargs)        Translate a dot-namespaced key
    L    (key, **kwargs)        Lazy string variant
    set_lang (lang)             Switch active locale
    get_lang ()                 Get active locale
    detect_lang ()              Detect locale from env vars

Extended API:
    t    (key, count=None, **kwargs)   Plural-aware translate
    set_locale (lang)                  Alias for set_lang
    current_locale ()                  Alias for get_lang
    available_locales ()               List of loaded locales
    reload_locales ()                  Force re-read YAML
    get_safety_relax_markers ()        Locale-aware safety markers

Locale resolution order:
    1. requested locale
    2. requested locale base (zh-CN -> zh)
    3. en (fallback)

Files: <dir>/locales/<locale>.yaml — dot-namespaced flat keys.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any

_LOCALE_DIR: Path = Path(__file__).parent / "locales"
_FALLBACK_LOCALE: str = "en"
_DEFAULT_LOCALE: str = "en"
_SUPPORTED_LOCALES: tuple[str, ...] = ("en", "zh-CN", "ja", "ko")

_PLURAL_SUFFIXES: tuple[str, ...] = ("_zero", "_one", "_two", "_few", "_many", "_other")
_PLURAL_RULES: dict[str, tuple[str, ...]] = {
    "en": ("_one", "_other"),
    "zh-CN": ("_other",),
    "ja": ("_other",),
    "ko": ("_other",),
}

_lock = threading.RLock()
_catalogs: dict[str, dict[str, Any]] = {}
_mtimes: dict[str, float] = {}
_current: str = "en"


def _locale_base(lang: str) -> str:
    if "_" in lang:
        return lang.split("_", 1)[0]
    if "-" in lang:
        return lang.split("-", 1)[0]
    return lang


def _catalog_alias(lang: str) -> str:
    if lang == "zh":
        return "zh-CN"
    return lang


def _candidate_locales(lang: str) -> list[str]:
    catalog = _catalog_alias(lang)
    base = _locale_base(catalog)
    out: list[str] = []
    if catalog in _SUPPORTED_LOCALES:
        out.append(catalog)
    if base in _SUPPORTED_LOCALES and base not in out:
        out.append(base)
    if catalog == "zh-CN" and "zh" not in out:
        out.append("zh")
    if _FALLBACK_LOCALE not in out:
        out.append(_FALLBACK_LOCALE)
    return out


def _load_locale(locale: str) -> dict[str, Any]:
    path = _LOCALE_DIR / f"{locale}.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        try:
            from ruamel.yaml import YAML

            yaml_loader = YAML(typ="safe")
            with path.open(encoding="utf-8") as f:
                data = yaml_loader.load(f)
                return dict(data or {})
        except ImportError:
            return _parse_simple_yaml(path.read_text(encoding="utf-8"))
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return {}
    return dict(data or {})


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r'^"([^"]+)"\s*:\s*(.*)$', line)
        if not m:
            continue
        key, raw = m.group(1), m.group(2).strip()
        if raw.startswith('"') and raw.endswith('"'):
            val = raw[1:-1]
            val = (
                val.replace("\\\\", "\x00")
                .replace("\\n", "\n")
                .replace('\\"', '"')
                .replace("\x00", "\\")
            )
            out[key] = val
    return out


def _maybe_reload() -> None:
    for locale in _SUPPORTED_LOCALES:
        path = _LOCALE_DIR / f"{locale}.yaml"
        if not path.exists():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if locale not in _catalogs or _mtimes.get(locale) != mtime:
            _catalogs[locale] = _load_locale(locale)
            _mtimes[locale] = mtime


def reload_locales() -> None:
    with _lock:
        _catalogs.clear()
        _mtimes.clear()
        _maybe_reload()


def available_locales() -> list[str]:
    with _lock:
        _maybe_reload()
        return [loc for loc in _SUPPORTED_LOCALES if _catalogs.get(loc)]


def detect_lang() -> str:
    for var in ("ECHO_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var, "")
        if not val:
            continue
        if val.startswith("zh"):
            return "zh-CN"
        for loc in _SUPPORTED_LOCALES:
            if val.startswith(loc):
                return loc
    return _DEFAULT_LOCALE


def set_lang(lang: str) -> None:
    global _current
    with _lock:
        _maybe_reload()
        if lang in _SUPPORTED_LOCALES:
            _current = lang
        elif lang == "zh":
            _current = "zh"
        else:
            base = _locale_base(lang)
            if base in _SUPPORTED_LOCALES:
                _current = base
            elif base == "zh":
                _current = "zh"
            else:
                _current = _FALLBACK_LOCALE


def set_locale(lang: str) -> None:
    set_lang(lang)


def get_lang() -> str:
    with _lock:
        _maybe_reload()
        return _current


def current_locale() -> str:
    return get_lang()


def _plural_suffix(locale: str, count: int) -> str:
    if locale in ("zh-CN", "ja", "ko"):
        return "_other"
    if count == 0:
        return "_zero"
    if count == 1:
        return "_one"
    if count == 2:
        return "_two"
    return "_other"


def _lookup(key: str, locale: str) -> Any:
    for cand in _candidate_locales(locale):
        cat = _catalogs.get(cand)
        if not cat:
            continue
        if key in cat:
            return cat[key]
    return None


def _plural_lookup(key: str, locale: str, count: int) -> Any:
    suffix = _plural_suffix(locale, count)
    plural_key = f"{key}{suffix}"
    val = _lookup(plural_key, locale)
    if val is not None:
        return val
    return _lookup(key, locale)


def _interpolate(template: Any, **kwargs: Any) -> str:
    if not isinstance(template, str):
        return template if template is not None else ""
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template


def _resolve_key(key: str, locale: str, count: int | None) -> Any:
    if count is not None and isinstance(count, int):
        return _plural_lookup(key, locale, count)
    return _lookup(key, locale)


def _(key: str, **kwargs: Any) -> str:
    locale = get_lang()
    template = _resolve_key(
        key, locale, kwargs.get("count") if isinstance(kwargs.get("count"), int) else None
    )
    if template is None:
        return key
    if "count" in kwargs and isinstance(kwargs["count"], int):
        kwargs = {k: v for k, v in kwargs.items() if k != "count"}
    return _interpolate(template, **kwargs)


def t(key: str, *, count: int | None = None, **kwargs: Any) -> str:
    locale = get_lang()
    template = _resolve_key(key, locale, count)
    if template is None:
        return key
    return _interpolate(template, **kwargs)


class _LazyString:
    __slots__ = ("_key", "_kwargs", "_count")

    def __init__(self, key: str, *, count: int | None = None, **kwargs: Any) -> None:
        self._key = key
        self._count = count
        self._kwargs = kwargs

    def __str__(self) -> str:
        if self._count is not None:
            return t(self._key, count=self._count, **self._kwargs)
        return _(self._key, **self._kwargs)

    def __repr__(self) -> str:
        return f"LazyString({self._key!r})"


def L(key: str, **kwargs: Any) -> _LazyString:  # noqa: N802
    count = kwargs.pop("count", None)
    if count is not None and not isinstance(count, int):
        kwargs["count"] = count
        count = None
    return _LazyString(key, count=count, **kwargs)


def get_safety_relax_markers() -> list[str]:
    """Return the union of safety-relaxation markers across all locales.

    Safety relaxation detection is language-agnostic: a Chinese prompt
    that says "跳过验证" should be caught whether the user interface is
    in English, Chinese, or any other language. We therefore return
    the union of every locale's marker list, deduplicated, regardless
    of the active locale.

    Used by PromptEvolver._mutation_relaxes_safety() to detect mutations
    that would push the agent toward skipping safety checks.
    """
    seen: set[str] = set()
    out: list[str] = []
    for locale in available_locales():
        catalog = _catalogs.get(locale)
        if not catalog:
            continue
        for key in (
            "safety.relax_markers",
            "safety.relax_markers_zh",
            "safety.relax_markers_ja",
            "safety.relax_markers_ko",
        ):
            val = catalog.get(key)
            if isinstance(val, list):
                for marker in val:
                    if not isinstance(marker, str):
                        continue
                    if marker in seen:
                        continue
                    seen.add(marker)
                    out.append(marker)
    return out


def get_marker(key: str, default: str | None = None, **kwargs: Any) -> str:
    """Lookup a locale-aware UI string with explicit default if missing."""
    val = _lookup(key, get_lang())
    if val is None:
        return default if default is not None else key
    if not isinstance(val, str):
        return default if default is not None else key
    return _interpolate(val, **kwargs)


with _lock:
    _maybe_reload()
    _current = detect_lang()
