"""Regression tests for §65 / §66 / §67 / §69 / §70 — final batch.

* §65: shell injection (subprocess shell=True / os.system / os.popen).
* §66: unsafe deserialization (pickle / yaml.load default / marshal).
* §67: network call inside loop body.
* §69: same string literal repeated 3+ times in one payload.
* §70: time/size-unit magic numbers as bare literals.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    _magic_number_guard,
    _network_in_loop_guard,
    _repeated_literal_guard,
    _shell_injection_guard,
    _unsafe_deser_guard,
)
from runtime.core.cerebrum.react_parsing import (
    _detect_magic_numbers_in_payload,
    _detect_repeated_literals_in_payload,
    _detect_shell_injection_in_payload,
    _detect_unsafe_deser_in_payload,
    _payload_has_network_in_loop,
    _step_introduces_magic_number,
    _step_introduces_network_in_loop,
    _step_introduces_repeated_literal,
    _step_introduces_shell_injection,
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
# §65 — shell injection
# ══════════════════════════════════════════════════════════════════


class TestDetectShellInjection:
    def test_subprocess_shell_true(self) -> None:
        assert _detect_shell_injection_in_payload(
            "subprocess.run(cmd, shell=True)",
        )

    def test_os_system(self) -> None:
        assert _detect_shell_injection_in_payload("os.system(cmd)")

    def test_os_popen(self) -> None:
        assert _detect_shell_injection_in_payload("os.popen(cmd)")

    def test_subprocess_argv_silent(self) -> None:
        assert (
            _detect_shell_injection_in_payload(
                "subprocess.run(['ls', '-la'])",
            )
            == []
        )

    def test_clean_silent(self) -> None:
        assert _detect_shell_injection_in_payload("def hello(): return 1") == []


class TestStepIntroducesShellInjection:
    def test_runtime_new_shell_true(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x", '
                '"new_string": "subprocess.run(cmd, shell=True)"})'
            ),
        )
        assert _step_introduces_shell_injection(step)

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_foo.py", '
                '"old_string": "x", '
                '"new_string": "subprocess.run(cmd, shell=True)"})'
            ),
        )
        assert not _step_introduces_shell_injection(step)


class TestShellInjectionGuard:
    def test_non_code_mode_still_blocks_shell_injection(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "os.system(cmd)"})'
                ),
            ),
        ]
        msg = _shell_injection_guard(steps, "done", is_code_mode=False)
        assert msg is not None
        assert "os.system" in msg

    def test_clean_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert _shell_injection_guard(steps, "done", is_code_mode=True) is None

    def test_shell_true_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "subprocess.run(cmd, shell=True)"})'
                ),
            ),
        ]
        msg = _shell_injection_guard(steps, "done", is_code_mode=True)
        assert msg is not None
        assert "runtime/foo.py" in msg


# ══════════════════════════════════════════════════════════════════
# §66 — unsafe deserialization
# ══════════════════════════════════════════════════════════════════


class TestDetectUnsafeDeser:
    def test_pickle_loads(self) -> None:
        hits = _detect_unsafe_deser_in_payload("data = pickle.loads(blob)")
        assert any("pickle" in h for h in hits)

    def test_marshal_loads(self) -> None:
        hits = _detect_unsafe_deser_in_payload("data = marshal.loads(blob)")
        assert any("marshal" in h for h in hits)

    def test_yaml_load_default_unsafe(self) -> None:
        hits = _detect_unsafe_deser_in_payload("config = yaml.load(text)")
        assert any("yaml" in h for h in hits)

    def test_yaml_safe_load_silent(self) -> None:
        # ``yaml.safe_load`` is the safe variant; not flagged.
        # ``yaml.load(text, Loader=SafeLoader)`` also silent.
        assert (
            _detect_unsafe_deser_in_payload(
                "config = yaml.load(text, Loader=SafeLoader)",
            )
            == []
        )

    def test_clean_silent(self) -> None:
        assert _detect_unsafe_deser_in_payload("data = json.loads(text)") == []


class TestUnsafeDeserGuard:
    def test_pickle_loads_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "data = pickle.loads(blob)"})'
                ),
            ),
        ]
        msg = _unsafe_deser_guard(steps, "done", is_code_mode=True)
        assert msg is not None
        assert "runtime/foo.py" in msg

    def test_yaml_safe_load_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "config = yaml.safe_load(text)"})'
                ),
            ),
        ]
        assert _unsafe_deser_guard(steps, "done", is_code_mode=True) is None

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "data = pickle.loads(blob)"})'
                ),
            ),
        ]
        assert (
            _unsafe_deser_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §67 — network in loop
# ══════════════════════════════════════════════════════════════════


class TestPayloadHasNetworkInLoop:
    def test_for_with_requests(self) -> None:
        text = "for x in items:\n    requests.get(url)\n"
        assert _payload_has_network_in_loop(text)

    def test_for_with_httpx(self) -> None:
        text = "for x in items:\n    httpx.get(url)\n"
        assert _payload_has_network_in_loop(text)

    def test_while_with_urlopen(self) -> None:
        text = "while flag:\n    urlopen(url)\n"
        assert _payload_has_network_in_loop(text)

    def test_loop_no_network_silent(self) -> None:
        text = "for x in items:\n    print(x)\n"
        assert not _payload_has_network_in_loop(text)

    def test_network_outside_loop_silent(self) -> None:
        text = "result = requests.get(url)\nfor x in items:\n    pass\n"
        assert not _payload_has_network_in_loop(text)


class TestStepIntroducesNetworkInLoop:
    def test_runtime_new(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x = 1", '
                '"new_string": "for x in items:\\n    requests.get(url)"})'
            ),
        )
        assert _step_introduces_network_in_loop(step)

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_foo.py", '
                '"old_string": "x", '
                '"new_string": "for x in items:\\n    requests.get(url)"})'
            ),
        )
        assert not _step_introduces_network_in_loop(step)


class TestNetworkInLoopGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "for x in items:\\n    requests.get(url)"})'
                ),
            ),
        ]
        assert _network_in_loop_guard(steps, "done", is_code_mode=False) is None

    def test_clean_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert _network_in_loop_guard(steps, "done", is_code_mode=True) is None

    def test_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x = 1", '
                    '"new_string": "for x in items:\\n    requests.get(url)"})'
                ),
            ),
        ]
        msg = _network_in_loop_guard(steps, "done", is_code_mode=True)
        assert msg is not None
        assert "runtime/foo.py" in msg


# ══════════════════════════════════════════════════════════════════
# §69 — repeated string literals
# ══════════════════════════════════════════════════════════════════


class TestDetectRepeatedLiterals:
    def test_three_repeats_detected(self) -> None:
        text = 'a = "runtime/safety"\nb = "runtime/safety"\nc = "runtime/safety"\n'
        repeats = _detect_repeated_literals_in_payload(text)
        assert ("runtime/safety", 3) in repeats

    def test_two_repeats_silent(self) -> None:
        text = 'a = "abcdefghi"\nb = "abcdefghi"\n'
        assert _detect_repeated_literals_in_payload(text) == []

    def test_short_strings_silent(self) -> None:
        # Strings < 8 chars not counted.
        text = 'a = "x"\nb = "x"\nc = "x"\n'
        assert _detect_repeated_literals_in_payload(text) == []

    def test_clean_silent(self) -> None:
        assert _detect_repeated_literals_in_payload("def hello(): return 1") == []


class TestStepIntroducesRepeatedLiteral:
    def test_runtime_new_repeats(self) -> None:
        step = _step(
            1,
            action=(
                'write_text_file({"path": "runtime/foo.py", '
                '"content": "a = \\"runtime/safety\\"\\nb = \\"runtime/safety\\"\\nc = \\"runtime/safety\\""})'
            ),
        )
        repeats = _step_introduces_repeated_literal(step)
        assert any(s == "runtime/safety" for s, _n in repeats)

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'write_text_file({"path": "tests/test_foo.py", '
                '"content": "a = \\"runtime/safety\\"\\nb = \\"runtime/safety\\"\\nc = \\"runtime/safety\\""})'
            ),
        )
        assert _step_introduces_repeated_literal(step) == []


class TestRepeatedLiteralGuard:
    def test_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'write_text_file({"path": "runtime/foo.py", '
                    '"content": "a = \\"runtime/safety\\"\\nb = \\"runtime/safety\\"\\nc = \\"runtime/safety\\""})'
                ),
            ),
        ]
        msg = _repeated_literal_guard(steps, "done", is_code_mode=True)
        assert msg is not None
        assert "runtime/safety" in msg

    def test_silent_when_under_threshold(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'write_text_file({"path": "runtime/foo.py", '
                    '"content": "a = \\"runtime/safety\\"\\nb = \\"runtime/safety\\""})'
                ),
            ),
        ]
        assert _repeated_literal_guard(steps, "done", is_code_mode=True) is None


# ══════════════════════════════════════════════════════════════════
# §70 — magic number
# ══════════════════════════════════════════════════════════════════


class TestDetectMagicNumbers:
    def test_one_day_seconds(self) -> None:
        assert 86400 in _detect_magic_numbers_in_payload("if x > 86400:")

    def test_one_hour_seconds(self) -> None:
        assert 3600 in _detect_magic_numbers_in_payload("timeout = 3600")

    def test_kibibyte(self) -> None:
        assert 1024 in _detect_magic_numbers_in_payload("buf = bytes(1024)")

    def test_random_number_silent(self) -> None:
        # 12345 not a known time/size unit.
        assert _detect_magic_numbers_in_payload("x = 12345") == []


class TestStepIntroducesMagicNumber:
    def test_runtime_new(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x = 1", '
                '"new_string": "if elapsed > 86400:\\n    pass"})'
            ),
        )
        assert 86400 in _step_introduces_magic_number(step)

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_foo.py", '
                '"old_string": "x", '
                '"new_string": "if x > 86400: pass"})'
            ),
        )
        assert _step_introduces_magic_number(step) == []


class TestMagicNumberGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "if elapsed > 86400: pass"})'
                ),
            ),
        ]
        assert _magic_number_guard(steps, "done", is_code_mode=False) is None

    def test_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x = 1", '
                    '"new_string": "if elapsed > 86400: pass"})'
                ),
            ),
        ]
        msg = _magic_number_guard(steps, "done", is_code_mode=True)
        assert msg is not None
        assert "86400" in msg

    def test_clean_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert _magic_number_guard(steps, "done", is_code_mode=True) is None
