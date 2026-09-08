"""Public engine imports stay compatible without eager heavy dependencies."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module, excluded",
    [
        ("runtime.core.cerebrum.react_types", "runtime.core.cerebrum.llm_planner"),
        ("runtime.execution.codex_backend.types", "runtime.execution.codex_backend.account"),
        (
            "runtime.execution.codex_backend.types",
            "runtime.execution.codex_backend.responses_proxy",
        ),
        ("runtime.execution.tool_engine.host_tool_broker", "runtime.execution.codex_backend"),
        ("runtime.execution.tool_engine.role_instructions", "runtime.execution.codex_backend"),
    ],
)
def test_lightweight_import_does_not_load_unused_engine(module, excluded):
    subprocess.run(
        [
            sys.executable,
            "-c",
            f"import importlib,sys; importlib.import_module({module!r}); assert {excluded!r} not in sys.modules",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("package", ["runtime.core.cerebrum", "runtime.execution.codex_backend"])
def test_all_public_exports_keep_identity_and_star_import_compatibility(package):
    code = f"""
import importlib
package = importlib.import_module({package!r})
assert set(package.__all__).issubset(dir(package))
for name, module in package._EXPORT_MODULES.items():
    expected = getattr(importlib.import_module(module, package.__name__), name)
    assert getattr(package, name) is expected
    assert package.__dict__[name] is expected
namespace = {{}}
exec("from {package} import *", namespace)
assert all(namespace[name] is getattr(package, name) for name in package.__all__)
try:
    getattr(package, "does_not_exist")
except AttributeError:
    pass
else:
    raise AssertionError("unknown export must raise AttributeError")
"""
    subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True, timeout=30
    )
