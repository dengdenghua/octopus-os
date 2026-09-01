"""Regression tests for the §21 / §22 / §23 guards added in this slice.

* §21: ``_signature_changed_without_typecheck_guard`` — public def
  parameter list / return annotation changed but no mypy/pyright/pyrefly
  ran.
* §22: ``_wire_schema_change_without_compat_test_guard`` — protocol/
  anthropic_compat / openai_gateway code touched without an
  accompanying wire-shape contract test edit.
* §23: ``_new_third_party_import_without_dep_guard`` — runtime code
  added ``import X`` for a non-stdlib, non-first-party package without
  a write to pyproject.toml / requirements.

Same conventions as the §19 / §20 test files: build small ReActStep
fixtures, assert the guard returns either ``None`` (silent) or a
non-empty string (fired).
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    _new_third_party_import_without_dep_guard,
    _signature_changed_without_typecheck_guard,
    _wire_schema_change_without_compat_test_guard,
)
from runtime.core.cerebrum.react_parsing import (
    _extract_public_signatures,
    _is_third_party_module,
    _is_wire_contract_test_path,
    _is_wire_schema_path,
    _new_third_party_imports_in_payload,
    _step_changed_public_signature,
    _step_edits_wire_schema,
    _step_introduces_third_party_imports,
    _step_writes_dep_manifest,
)
from runtime.core.cerebrum.react_types import ReActStep


def _step(
    iteration: int,
    *,
    thought: str = "",
    action: str = "",
    observation: str = "",
) -> ReActStep:
    return ReActStep(
        iteration=iteration,
        thought=thought,
        action=action,
        observation=observation,
    )


# ══════════════════════════════════════════════════════════════════
# §21 — signature-change guard
# ══════════════════════════════════════════════════════════════════


class TestExtractPublicSignatures:
    def test_simple_def(self) -> None:
        sigs = _extract_public_signatures("def hello(a, b):\n    return 1\n")
        assert sigs == {"hello": ("a, b", "")}

    def test_def_with_return_annotation(self) -> None:
        sigs = _extract_public_signatures("def hello(a: int) -> str:\n    return ''\n")
        assert sigs == {"hello": ("a: int", "str")}

    def test_async_def(self) -> None:
        sigs = _extract_public_signatures("async def fetch(url: str) -> bytes:\n    pass\n")
        assert sigs == {"fetch": ("url: str", "bytes")}

    def test_private_excluded(self) -> None:
        sigs = _extract_public_signatures("def _hidden(a):\n    pass\n")
        assert sigs == {}

    def test_multiple_defs(self) -> None:
        text = "def a():\n    pass\ndef b(x):\n    pass\n"
        assert set(_extract_public_signatures(text)) == {"a", "b"}


class TestStepChangedPublicSignature:
    def test_same_signature_no_change(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "def hello(a, b):\\n    return 1", '
                '"new_string": "def hello(a, b):\\n    return 2"})'
            ),
        )
        assert not _step_changed_public_signature(step)

    def test_param_added(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "def hello(a, b):", '
                '"new_string": "def hello(a, b, c):"})'
            ),
        )
        assert _step_changed_public_signature(step)

    def test_return_annotation_changed(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "def hello(a) -> int:", '
                '"new_string": "def hello(a) -> str:"})'
            ),
        )
        assert _step_changed_public_signature(step)

    def test_private_change_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "def _hidden(a):", '
                '"new_string": "def _hidden(a, b):"})'
            ),
        )
        assert not _step_changed_public_signature(step)

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_foo.py", '
                '"old_string": "def hello(a):", '
                '"new_string": "def hello(a, b):"})'
            ),
        )
        assert not _step_changed_public_signature(step)

    def test_write_text_file_skipped(self) -> None:
        # No old_string to compare against — guard can't tell.
        step = _step(
            1,
            action='write_text_file({"path": "runtime/foo.py", "content": "def hello(a):\\n    pass\\n"})',
        )
        assert not _step_changed_public_signature(step)

    def test_multi_edit_change_detected(self) -> None:
        step = _step(
            1,
            action=(
                'multi_edit_file({"path": "runtime/foo.py", "edits": ['
                '{"old_string": "def hello(a):", "new_string": "def hello(a, b):"}'
                "]})"
            ),
        )
        assert _step_changed_public_signature(step)


class TestSignatureChangedWithoutTypecheckGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "def f(a):", "new_string": "def f(a, b):"})',
            ),
        ]
        assert (
            _signature_changed_without_typecheck_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_signature_change_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _signature_changed_without_typecheck_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_signature_change_no_typecheck_fires(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "def f(a):", "new_string": "def f(a, b):"})',
            ),
        ]
        msg = _signature_changed_without_typecheck_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "typecheck" in msg.lower()

    def test_signature_change_with_mypy_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "def f(a):", "new_string": "def f(a, b):"})',
            ),
            _step(2, action='exec_shell({"command": "mypy runtime/foo.py"})'),
        ]
        assert (
            _signature_changed_without_typecheck_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_signature_change_with_pyright_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "def f(a):", "new_string": "def f(a, b):"})',
            ),
            _step(2, action='exec_shell({"command": "pyright runtime/"})'),
        ]
        assert (
            _signature_changed_without_typecheck_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_ruff_alone_does_not_count(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "def f(a):", "new_string": "def f(a, b):"})',
            ),
            _step(2, action='exec_shell({"command": "ruff check runtime/foo.py"})'),
        ]
        assert (
            _signature_changed_without_typecheck_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is not None
        )

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "def f(a):", "new_string": "def f(a, b):"})',
            ),
        ]
        assert (
            _signature_changed_without_typecheck_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §22 — wire-schema guard
# ══════════════════════════════════════════════════════════════════


class TestIsWireSchemaPath:
    def test_protocol_items(self) -> None:
        assert _is_wire_schema_path("runtime/protocol/items.py")

    def test_anthropic_compat(self) -> None:
        assert _is_wire_schema_path("runtime/sensing/siphon/anthropic_compat/router.py")

    def test_openai_gateway(self) -> None:
        assert _is_wire_schema_path("runtime/sensing/siphon/openai_gateway/models.py")

    def test_random_runtime_file_not_wire(self) -> None:
        assert not _is_wire_schema_path("runtime/core/cerebrum/react_loop.py")

    def test_test_file_not_wire(self) -> None:
        # The guard fires on schema EDITS, not on test edits — but the
        # detector itself just classifies paths.
        assert not _is_wire_schema_path("tests/test_react_loop.py")


class TestIsWireContractTestPath:
    def test_anthropic_compat_test(self) -> None:
        assert _is_wire_contract_test_path("tests/test_anthropic_compat_router.py")

    def test_openai_gateway_test(self) -> None:
        assert _is_wire_contract_test_path("tests/test_openai_gateway_sse_contract.py")

    def test_random_test_not_wire(self) -> None:
        assert not _is_wire_contract_test_path("tests/test_react_guards_inflight.py")

    def test_runtime_file_not_test(self) -> None:
        assert not _is_wire_contract_test_path("runtime/protocol/items.py")


class TestStepEditsWireSchema:
    def test_anthropic_compat_edit(self) -> None:
        step = _step(
            1,
            action='edit_file({"path": "runtime/sensing/siphon/anthropic_compat/models.py", "old_string": "x", "new_string": "y"})',
        )
        assert _step_edits_wire_schema(step)

    def test_protocol_items_edit(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "runtime/protocol/items.py", "content": "x"})',
        )
        assert _step_edits_wire_schema(step)

    def test_unrelated_runtime_edit(self) -> None:
        step = _step(
            1,
            action='edit_file({"path": "runtime/core/cerebrum/react_loop.py", "old_string": "x", "new_string": "y"})',
        )
        assert not _step_edits_wire_schema(step)


class TestWireSchemaGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/protocol/items.py", "old_string": "x", "new_string": "y"})',
            ),
        ]
        assert (
            _wire_schema_change_without_compat_test_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_wire_edit_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _wire_schema_change_without_compat_test_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_wire_edit_no_contract_test_fires(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/protocol/items.py", "old_string": "x", "new_string": "y"})',
            ),
        ]
        msg = _wire_schema_change_without_compat_test_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "wire-shape" in msg.lower() or "wire" in msg.lower()

    def test_wire_edit_with_contract_test_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/protocol/items.py", "old_string": "x", "new_string": "y"})',
            ),
            _step(
                2,
                action='write_text_file({"path": "tests/test_anthropic_compat.py", "content": "def test_x():\\n    pass\\n"})',
            ),
        ]
        assert (
            _wire_schema_change_without_compat_test_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_wire_edit_with_random_test_still_fires(self) -> None:
        # Editing a non-wire test is not enough — must be a contract test.
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/protocol/items.py", "old_string": "x", "new_string": "y"})',
            ),
            _step(
                2,
                action='write_text_file({"path": "tests/test_random.py", "content": "def test_y():\\n    pass\\n"})',
            ),
        ]
        assert (
            _wire_schema_change_without_compat_test_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is not None
        )

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/protocol/items.py", "old_string": "x", "new_string": "y"})',
            ),
        ]
        assert (
            _wire_schema_change_without_compat_test_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §23 — third-party import guard
# ══════════════════════════════════════════════════════════════════


class TestIsThirdPartyModule:
    def test_stdlib(self) -> None:
        assert not _is_third_party_module("os")
        assert not _is_third_party_module("json")
        assert not _is_third_party_module("typing")

    def test_first_party(self) -> None:
        assert not _is_third_party_module("runtime")
        assert not _is_third_party_module("tests")

    def test_third_party(self) -> None:
        assert _is_third_party_module("requests")
        assert _is_third_party_module("httpx")
        assert _is_third_party_module("pydantic")

    def test_dunder_excluded(self) -> None:
        assert not _is_third_party_module("__future__")


class TestNewThirdPartyImportsInPayload:
    def test_simple_import(self) -> None:
        assert _new_third_party_imports_in_payload("import requests\n") == {"requests"}

    def test_from_import(self) -> None:
        assert _new_third_party_imports_in_payload("from httpx import AsyncClient\n") == {"httpx"}

    def test_dotted_collapses(self) -> None:
        # ``from langfuse.client import X`` → top-level is ``langfuse``.
        assert _new_third_party_imports_in_payload("from langfuse.client import X\n") == {
            "langfuse"
        }

    def test_stdlib_excluded(self) -> None:
        assert _new_third_party_imports_in_payload("import os\nfrom typing import Any\n") == set()

    def test_first_party_excluded(self) -> None:
        assert _new_third_party_imports_in_payload("from runtime.foo import bar\n") == set()


class TestStepIntroducesThirdPartyImports:
    def test_runtime_edit_with_new_requests(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "import os", '
                '"new_string": "import os\\nimport requests"})'
            ),
        )
        assert _step_introduces_third_party_imports(step) == {"requests"}

    def test_pre_existing_import_not_flagged(self) -> None:
        # ``requests`` was in old too — not a new addition.
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "import requests", '
                '"new_string": "import requests\\n# tweak"})'
            ),
        )
        assert _step_introduces_third_party_imports(step) == set()

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_foo.py", '
                '"old_string": "x", "new_string": "import requests"})'
            ),
        )
        assert _step_introduces_third_party_imports(step) == set()

    def test_non_python_skipped(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "frontend/x.tsx", "content": "import requests"})',
        )
        assert _step_introduces_third_party_imports(step) == set()


class TestStepWritesDepManifest:
    def test_pyproject(self) -> None:
        step = _step(1, action='write_text_file({"path": "pyproject.toml", "content": "x"})')
        assert _step_writes_dep_manifest(step)

    def test_requirements_txt(self) -> None:
        step = _step(1, action='write_text_file({"path": "requirements.txt", "content": "x"})')
        assert _step_writes_dep_manifest(step)

    def test_runtime_py_not_manifest(self) -> None:
        step = _step(1, action='write_text_file({"path": "runtime/foo.py", "content": "x"})')
        assert not _step_writes_dep_manifest(step)


class TestNewThirdPartyImportWithoutDepGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", "new_string": "import requests"})'
                ),
            ),
        ]
        assert (
            _new_third_party_import_without_dep_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_new_imports_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _new_third_party_import_without_dep_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_new_import_no_manifest_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", "new_string": "import requests\\nx = 1"})'
                ),
            ),
        ]
        msg = _new_third_party_import_without_dep_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "requests" in msg

    def test_new_import_with_pyproject_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", "new_string": "import requests"})'
                ),
            ),
            _step(
                2,
                action='edit_file({"path": "pyproject.toml", "old_string": "deps = []", "new_string": "deps = [\\"requests\\"]"})',
            ),
        ]
        assert (
            _new_third_party_import_without_dep_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_first_party_import_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", "new_string": "from runtime.bar import baz"})'
                ),
            ),
        ]
        assert (
            _new_third_party_import_without_dep_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", "new_string": "import requests"})'
                ),
            ),
        ]
        assert (
            _new_third_party_import_without_dep_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )
