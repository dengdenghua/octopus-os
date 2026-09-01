"""Chat page HTML (head + CSS + JS) for the zero-dependency fallback UI.

This module is a thin concatenation shim over the split ``_chat_page_*``
submodules. The public API is a single ``_CHAT_HTML`` string, imported by
``chat_page.py``.
"""

from __future__ import annotations

from ._chat_page_css import _HEAD_CSS
from ._chat_page_js_chat import _CHAT_JS
from ._chat_page_js_login import _LOGIN_JS
from ._chat_page_js_models import _MODELS_JS

_CHAT_HTML = _HEAD_CSS + _LOGIN_JS + _CHAT_JS + _MODELS_JS

__all__ = ["_CHAT_HTML"]
