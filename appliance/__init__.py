"""Echo OS appliance layer.

OS 专属代码统一放在这个顶层包里，与内建 Echo Agent runtime 一起发行。
"""

from __future__ import annotations

import os


def _install_legacy_environment_aliases() -> None:
    """Promote pre-Echo environment settings before appliance modules load."""

    legacy_prefix = "OCTO" + "PUS_"
    for name, value in tuple(os.environ.items()):
        if name.startswith(legacy_prefix):
            os.environ.setdefault(f"ECHO_{name.removeprefix(legacy_prefix)}", value)


_install_legacy_environment_aliases()
