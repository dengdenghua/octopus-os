"""mypy ratchet · parse + baseline-comparison logic.

The ratchet freezes today's mypy errors and fails only on NEW ones. These
tests lock the parsing (line-independent keys) and the multiset diff that
makes "one more copy of an existing error" still trip the gate.
"""

from __future__ import annotations

from collections import Counter

from tools.lint import mypy_ratchet
from tools.lint.mypy_ratchet import _collect_errors

_SAMPLE = """\
runtime/safety/auth/identity.py:107: error: Incompatible types in assignment (expression has type "tuple[str, ...]", variable has type "tuple[str]")  [assignment]
runtime/core/cerebrum/react_parsing.py:274: error: Bad thing here  [assignment]
runtime/core/cerebrum/react_parsing.py:274: note: this note line is ignored
runtime/x.py:9: error: No code on this one
Found 3 errors in 2 files (checked 5 source files)
"""


def test_collect_parses_errors_and_ignores_notes_and_summary():
    errors = _collect_errors(_SAMPLE)
    assert sum(errors.values()) == 3  # 3 errors, note + summary ignored
    # Key is path\tcode\tmessage — no line number.
    assert "runtime/core/cerebrum/react_parsing.py\tassignment\tBad thing here" in errors
    # Error with no code gets the "?" placeholder.
    assert "runtime/x.py\t?\tNo code on this one" in errors


def test_key_is_line_independent():
    a = _collect_errors("runtime/a.py:10: error: boom  [x]")
    b = _collect_errors("runtime/a.py:999: error: boom  [x]")
    assert a == b  # same error at a different line → same key


def test_new_error_is_a_multiset_difference():
    baseline = _collect_errors("runtime/a.py:1: error: boom  [x]\nruntime/a.py:2: error: boom  [x]")
    # Three copies now — one more than the two baselined.
    current = _collect_errors(
        "runtime/a.py:1: error: boom  [x]\n"
        "runtime/a.py:2: error: boom  [x]\n"
        "runtime/a.py:3: error: boom  [x]"
    )
    new = current - baseline
    assert sum(new.values()) == 1  # exactly the extra copy trips the gate


def test_fixed_error_is_baseline_minus_current():
    baseline = _collect_errors("runtime/a.py:1: error: boom  [x]")
    current: Counter[str] = Counter()  # error was fixed → gone
    assert sum((current - baseline).values()) == 0  # no new
    assert sum((baseline - current).values()) == 1  # one fixed (ratchet down)


def test_empty_baseline_writer_has_no_trailing_blank_line(monkeypatch, tmp_path):
    baseline_path = tmp_path / "mypy_baseline.txt"
    monkeypatch.setattr(mypy_ratchet, "_BASELINE_PATH", baseline_path)

    mypy_ratchet._write_baseline(Counter())

    text = baseline_path.read_text(encoding="utf-8")
    assert text.endswith("ratchet the count down.\n")
    assert not text.endswith("\n\n")


def test_main_fails_when_mypy_cannot_run(monkeypatch, capsys):
    monkeypatch.setattr(mypy_ratchet, "_run_mypy", lambda: (1, "python: No module named mypy"))
    monkeypatch.setattr("sys.argv", ["mypy_ratchet.py"])

    assert mypy_ratchet.main() == 2
    assert "type ratchet did not run" in capsys.readouterr().err

