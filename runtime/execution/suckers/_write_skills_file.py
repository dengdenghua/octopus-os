"""File write / append / edit primitives for write_skills · extracted from
write_skills.py.

These are the low-level UTF-8 file mutation tools that back the
``write_text_file`` / ``append_text_file`` / ``edit_text_file`` / ``edit_file`` /
``multi_edit_file`` skills.
"""

from __future__ import annotations

from typing import Any

from ._write_skills_common import _DEFAULT_MAX_BYTES, _ensure_sandbox


def _write_text_file(
    path: str = "",
    content: str = "",
    *,
    sandbox_dir: str | None = None,
    overwrite: bool = False,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    **_kw: Any,
) -> dict[str, Any]:
    if not path:
        return {"error": "missing path"}
    data = content.encode("utf-8")
    if len(data) > max_bytes:
        return {"error": f"content too large: {len(data)} > {max_bytes}"}

    resolved, err = _ensure_sandbox(path, sandbox_dir)
    if err:
        return {"error": err}
    if resolved.exists() and not overwrite:
        return {
            "error": "exists · pass overwrite=True to replace",
            "path": str(resolved),
        }
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(data)
    except OSError as e:
        return {"error": f"write_failed: {e}", "path": str(resolved)}

    # Write-after verification (Hermes write_file parity): confirm the bytes
    # actually landed on disk. A silent partial write (quota, CoW hiccup,
    # exotic FS) would otherwise look like success and waste a model turn.
    result: dict[str, Any] = {
        "path": str(resolved),
        "bytes_written": len(data),
        "overwrote": resolved.exists(),
    }
    try:
        on_disk = resolved.read_bytes()
    except OSError as e:
        result["verify_error"] = f"read_back_failed: {e}"
        return result
    if on_disk != data:
        result["verify_error"] = (
            f"read_back_mismatch: {len(on_disk)} bytes on disk vs {len(data)} expected"
        )
    else:
        result["verified"] = True
    return result


def _append_text_file(
    path: str = "",
    content: str = "",
    *,
    sandbox_dir: str | None = None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    **_kw: Any,
) -> dict[str, Any]:
    if not path:
        return {"error": "missing path"}
    data = content.encode("utf-8")
    if len(data) > max_bytes:
        return {"error": f"content too large: {len(data)} > {max_bytes}"}

    resolved, err = _ensure_sandbox(path, sandbox_dir)
    if err:
        return {"error": err}
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with resolved.open("ab") as f:
            f.write(data)
    except OSError as e:
        return {"error": f"append_failed: {e}", "path": str(resolved)}

    result: dict[str, Any] = {
        "path": str(resolved),
        "bytes_appended": len(data),
        "total_size": resolved.stat().st_size,
    }
    # Write-after verification: confirm the appended tail landed on disk.
    try:
        on_disk = resolved.read_bytes()
    except OSError as e:
        result["verify_error"] = f"read_back_failed: {e}"
        return result
    expected_tail = data
    if not on_disk.endswith(expected_tail):
        result["verify_error"] = (
            f"read_back_mismatch: appended tail not found on disk "
            f"({resolved.stat().st_size} bytes total)"
        )
    else:
        result["verified"] = True
    return result


def _edit_text_file(
    path: str = "",
    find: str = "",
    replace: str = "",
    *,
    sandbox_dir: str | None = None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    count: int = -1,  # Implementation note.
    **_kw: Any,
) -> dict[str, Any]:
    if not path:
        return {"error": "missing path"}
    if not find:
        return {"error": "find must be non-empty"}

    resolved, err = _ensure_sandbox(path, sandbox_dir)
    if err:
        return {"error": err}
    if not resolved.exists():
        return {"error": f"not found: {resolved}"}
    try:
        original = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"error": f"read_failed: {e}"}
    occurrences = original.count(find)
    if occurrences == 0:
        return {
            "error": "find pattern not present in file",
            "occurrences": 0,
        }
    if count < 0:
        new_text = original.replace(find, replace)
        replaced = occurrences
    else:
        new_text = original.replace(find, replace, count)
        replaced = min(occurrences, count)
    new_bytes = new_text.encode("utf-8")
    if len(new_bytes) > max_bytes:
        return {"error": f"result too large: {len(new_bytes)} > {max_bytes}"}
    try:
        resolved.write_bytes(new_bytes)
    except OSError as e:
        return {"error": f"write_failed: {e}"}

    result: dict[str, Any] = {
        "path": str(resolved),
        "occurrences": occurrences,
        "replaced": replaced,
        "new_size": len(new_bytes),
    }
    # Write-after verification: confirm the edit actually landed and the
    # find pattern is gone (or replaced as requested). A stale-cache or
    # partial write would otherwise read back as success.
    try:
        on_disk = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        result["verify_error"] = f"read_back_failed: {e}"
        return result
    if on_disk != new_text:
        result["verify_error"] = (
            f"read_back_mismatch: expected {len(new_text)} chars, got {len(on_disk)}"
        )
    elif count >= 0 and on_disk.count(find) != max(0, occurrences - count):
        result["verify_error"] = (
            f"read_back_mismatch: expected {max(0, occurrences - count)} "
            f"remaining occurrences of pattern, got {on_disk.count(find)}"
        )
    else:
        result["verified"] = True
    return result


def _edit_file(
    path: str = "",
    old_string: str = "",
    new_string: str = "",
    *,
    replace_all: bool = False,
    sandbox_dir: str | None = None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    **_kw: Any,
) -> dict[str, Any]:
    if not path:
        return {"error": "missing path"}
    if not old_string:
        return {"error": "old_string must be non-empty"}
    if old_string == new_string:
        return {"error": "no-op edit: old_string and new_string are identical"}

    resolved, err = _ensure_sandbox(path, sandbox_dir)
    if err:
        return {"error": err}
    if not resolved.exists():
        return {"error": f"not found: {resolved}"}
    try:
        original = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"error": f"read_failed: {e}"}

    occurrences = original.count(old_string)
    if occurrences == 0:
        # Hermes parity: detect the "edit already applied" case — the target
        # text is gone but the replacement is present, so the model is likely
        # retrying a completed edit rather than fixing a mismatch.
        if new_string and new_string in original:
            preview = new_string[:80].replace("\n", "\\n")
            return {
                "error": "edit_already_applied",
                "occurrences": 0,
                "hint": (
                    "old_string wasn't found, but new_string is already "
                    "present in the file — the edit appears to have been "
                    "applied already. No further action needed; if you "
                    "meant a different edit, re-read the file with "
                    "read_file(path) to see the current content."
                ),
                "new_string_present": True,
                "preview": preview,
            }
        # Help the model recover by hinting at the most common cause —
        # whitespace / line-ending / quoting drift between what they
        # remembered and what's actually on disk.
        preview = old_string[:80].replace("\n", "\\n")
        return {
            "error": "old_string_not_found",
            "occurrences": 0,
            "hint": (
                f"Searched for: {preview!r}. The exact bytes weren't "
                "found. Common causes: (1) whitespace/indentation "
                "differs from what you remember, (2) the file was "
                "modified by a previous tool call this turn, (3) "
                "different line endings (\\r\\n vs \\n). "
                "Re-read the file with read_file(path) and copy the "
                "block literally."
            ),
        }
    if not replace_all and occurrences != 1:
        # Show approximate locations so the model can disambiguate
        # by adding surrounding context.
        first_line = original[: original.index(old_string)].count("\n") + 1
        return {
            "error": "old_string_not_unique",
            "occurrences": occurrences,
            "first_match_line": first_line,
            "hint": (
                f"Found {occurrences} occurrences (first one at line "
                f"{first_line}). To disambiguate either: (a) extend "
                "old_string with surrounding lines so it matches "
                "exactly once, or (b) pass replace_all=True if you "
                "really want every occurrence changed."
            ),
        }

    new_text = (
        original.replace(old_string, new_string)
        if replace_all
        else original.replace(old_string, new_string, 1)
    )
    new_bytes = new_text.encode("utf-8")
    if len(new_bytes) > max_bytes:
        return {"error": f"result too large: {len(new_bytes)} > {max_bytes}"}
    try:
        resolved.write_bytes(new_bytes)
    except OSError as e:
        return {"error": f"write_failed: {e}"}

    result: dict[str, Any] = {
        "path": str(resolved),
        "occurrences": occurrences,
        "replaced": occurrences if replace_all else 1,
        "new_size": len(new_bytes),
    }
    try:
        on_disk = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        result["verify_error"] = f"read_back_failed: {e}"
        return result
    if on_disk != new_text:
        result["verify_error"] = (
            f"read_back_mismatch: expected {len(new_text)} chars, got {len(on_disk)}"
        )
    else:
        result["verified"] = True
    return result


def _multi_edit_file(
    path: str = "",
    edits: list[dict[str, Any]] | None = None,
    *,
    sandbox_dir: str | None = None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    **_kw: Any,
) -> dict[str, Any]:
    if not path:
        return {"error": "missing path"}
    if not edits:
        return {"error": "edits must be a non-empty list"}

    resolved, err = _ensure_sandbox(path, sandbox_dir)
    if err:
        return {"error": err}
    if not resolved.exists():
        return {"error": f"not found: {resolved}"}
    try:
        original = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"error": f"read_failed: {e}"}

    candidate = original
    normalized: list[tuple[str, str, bool]] = []
    for idx, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return {"error": f"edits[{idx}] must be an object"}
        old_string = str(edit.get("old_string", ""))
        new_string = str(edit.get("new_string", ""))
        replace_all = bool(edit.get("replace_all", False))
        if not old_string:
            return {"error": f"edits[{idx}].old_string must be non-empty"}
        if old_string == new_string:
            return {"error": f"edits[{idx}] is a no-op: old_string and new_string are identical"}
        occurrences = candidate.count(old_string)
        if occurrences == 0:
            preview = old_string[:80].replace("\n", "\\n")
            return {
                "error": f"edits[{idx}].old_string_not_found",
                "edit_index": idx,
                "occurrences": 0,
                "hint": (
                    f"Searched for: {preview!r}. Note: each edit "
                    "applies to the file AFTER previous edits in this "
                    "call. If a prior edit changed the surrounding "
                    "context, the later old_string may no longer match. "
                    "Re-read the file or split into separate edit_file "
                    "calls."
                ),
            }
        if not replace_all and occurrences != 1:
            first_line = candidate[: candidate.index(old_string)].count("\n") + 1
            return {
                "error": f"edits[{idx}].old_string_not_unique",
                "edit_index": idx,
                "occurrences": occurrences,
                "first_match_line": first_line,
                "hint": (
                    f"Found {occurrences} occurrences in edits[{idx}] "
                    f"(first at line {first_line}). Extend old_string "
                    "with surrounding lines or set replace_all=True "
                    "for this specific edit."
                ),
            }
        normalized.append((old_string, new_string, replace_all))
        candidate = (
            candidate.replace(old_string, new_string)
            if replace_all
            else candidate.replace(old_string, new_string, 1)
        )

    new_bytes = candidate.encode("utf-8")
    if len(new_bytes) > max_bytes:
        return {"error": f"result too large: {len(new_bytes)} > {max_bytes}"}
    try:
        resolved.write_bytes(new_bytes)
    except OSError as e:
        return {"error": f"write_failed: {e}"}

    result: dict[str, Any] = {
        "path": str(resolved),
        "edits": len(normalized),
        "new_size": len(new_bytes),
    }
    try:
        on_disk = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        result["verify_error"] = f"read_back_failed: {e}"
        return result
    if on_disk != candidate:
        result["verify_error"] = (
            f"read_back_mismatch: expected {len(candidate)} chars, got {len(on_disk)}"
        )
    else:
        result["verified"] = True
    return result
