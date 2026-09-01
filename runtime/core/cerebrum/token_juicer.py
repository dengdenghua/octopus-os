"""Token compression for tool observations before they enter the LLM
message stream.

Goal: when a tool produces verbose output (HTML page bodies,
JSON-RPC dumps, long search results), the model only needs the
*meaningful* slice. Trim it on the way INTO messages, leave the
full version intact in step.observation / journal so display +
guards keep their fidelity.

Compression passes (each is a no-op when it doesn't apply). Default ON
to keep the message stream lean on long turns; opt out by setting
ECHO_TOKEN_JUICE=0. The protected-pattern guard ensures critical
sentinels like `(工具失败)` and `[1/N]` headers are never stripped:

  1. HTML → Markdown-ish prose (drop tags, keep visible text, keep
     <code>/<pre> ranges intact)
  2. Long URL shortening: URLs > 80 chars become "<domain.com/...>"
  3. Consecutive duplicate-line dedup
  4. Bulky JSON-array trimming (lists > 12 items collapse to first
     5 + last 2 + count marker)
  5. Hard char cap (keeps last N chars after head, since errors
     usually live near the tail)

Sentinel patterns the runtime expects (e.g. `(工具失败)`,
`(real tool execution succeeded)`, `[1/N tool_name]` parallel-batch
headers) are NEVER stripped — the regex set guards them.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass

# Patterns the loop downstream depends on; never compress past them.
_PROTECTED_RE = re.compile(
    r"(\(工具失败\)|\(工具执行异常\)|\(工具未注册\)|"
    r"\(real tool execution succeeded\)|"
    r"\[\d+/\d+ [^\]]+\]|"
    r"\[自动诊断结果\]|\[关联文件预读\])",
)

_HTML_TAG_RE = re.compile(r"<[^<>]{1,500}>")
_HTML_SCRIPT_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_INDICATOR_RE = re.compile(r"<(html|body|div|p|span|a)\b", re.IGNORECASE)
_LONG_URL_RE = re.compile(r"https?://[^\s\"'<>\)]{80,}")
_DUPLICATE_LINE_RUN_RE = re.compile(r"(.+)(\n\1){3,}")
# Same shape but for JSON-escaped strings (tool outputs that
# embed stdout as a "stdout" field literally write "\n" not a real
# newline — a regex that only sees real \n misses these).
_DUPLICATE_ESCAPED_LINE_RUN_RE = re.compile(r"(.+?)(\\n\1){3,}")
_PARALLEL_SECTION_HEADER_RE = re.compile(r"(?m)^\[\d+/\d+ [^\]\n]+\]")


@dataclass(frozen=True)
class JuiceStats:
    """Before/after char counts for a single compression pass.

    The runtime logs these so we can quantify token savings on real
    workloads. Tokens ≈ chars / 4 for English, ≈ chars / 1.7 for
    CJK — both directions improve when chars drop.
    """

    before: int
    after: int
    passes: tuple[str, ...]

    @property
    def saved(self) -> int:
        return self.before - self.after

    @property
    def ratio(self) -> float:
        if self.before <= 0:
            return 1.0
        return self.after / self.before


def _strip_html(text: str) -> str:
    """Drop HTML tags but keep visible text. Preserves code/pre."""
    if not _HTML_INDICATOR_RE.search(text):
        return text
    # First nuke <script>/<style> blocks — their bodies are noise.
    cleaned = _HTML_SCRIPT_RE.sub(" ", text)
    # Strip remaining tags. _HTML_TAG_RE caps tag length at 500
    # chars so a malformed tag can't eat the whole string.
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    # Decode the most common entities.
    cleaned = (
        cleaned.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    # Collapse the whitespace explosion HTML stripping leaves behind.
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _shorten_long_urls(text: str) -> str:
    """https://very.long/.../...?a=…&b=… → <very.long/...>"""

    def _replace(m: re.Match[str]) -> str:
        url = m.group(0)
        m2 = re.match(r"https?://([^/]+)/?(.{0,12})", url)
        if not m2:
            return url
        host = m2.group(1)
        head = m2.group(2)
        return f"<{host}/{head}…>" if head else f"<{host}/…>"

    return _LONG_URL_RE.sub(_replace, text)


def _dedup_repeated_lines(text: str) -> str:
    """Collapse runs of 4+ IDENTICAL lines into "<line> ×N".

    Two shapes, both lossless (the collapsed lines are byte-for-byte
    equal to the survivor):
      1. Real newlines (most common — direct stdout)
      2. JSON-escaped \\n (when stdout is wrapped in a JSON field)

    Note: an earlier "similar-prefix run" collapse was removed. Lines
    sharing a prefix (`src/a.py: …`, `src/b.py: …`) are NOT redundant —
    each grep hit is distinct data the model may need, and there's no
    syntactic signal separating "redundant repetition" from "a list of
    distinct results that happen to share a prefix". Only exact
    duplicates can be dropped without guessing.
    """

    def _replace_run(m: re.Match[str]) -> str:
        line = m.group(1)
        # Count line separators (either real \n or escaped \\n).
        sep_count = max(
            m.group(0).count("\n"),
            m.group(0).count("\\n"),
        )
        n = sep_count + 1
        return f"{line}\n  …(× {n} times)"

    out = _DUPLICATE_LINE_RUN_RE.sub(_replace_run, text)
    out = _DUPLICATE_ESCAPED_LINE_RUN_RE.sub(_replace_run, out)
    return out  # noqa: RET504 — keep `out` named for readability


# Array trimming logic:
# The original regex ``\[(\s*\{.*?\}\s*,?\s*){13,}\]`` caused catastrophic
# backtracking on large inputs, burning the GIL and disconnecting clients.
#
# The fix below splits the problem:
# 1. A simple, linear-time state machine locates each ``[``'s matching ``]``
#    (handling nested brackets and quoted strings correctly). This never
#    backtracks.
# 2. Once an array's span is known, the original object-finding logic
#    (which is fine on its own, only dangerous when combined with the outer
#    nested-quantifier regex) extracts items and performs the head/tail
#    trimming. We no longer use ``json.JSONDecoder.raw_decode`` because
#    tool outputs are often not strictly valid JSON (trailing commas,
#    single quotes, Python repr, etc.) and we must not silently skip
#    compression on those cases.


def _find_json_array_end(text: str, start: int) -> int:
    """Find the index of the ``]`` that closes the array starting at ``start``.

    Linear-time bracket matching that respects JSON string literals
    (so ``[`` or ``]`` inside strings don't affect the depth).
    Returns -1 if the brackets don't balance.
    """
    depth = 0
    i = start
    n = len(text)
    in_string = False
    escape = False
    while i < n:
        ch = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def _trim_oversized_arrays(text: str) -> str:
    """When JSON output contains a long list of objects, keep first 5
    and last 2 — model rarely needs item #8 of 50.

    Uses a linear bracket-matching state machine to find arrays (no
    catastrophic backtracking), then applies the original lenient
    object extraction to produce the trimmed output.
    """

    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "[":
            end = _find_json_array_end(text, i)
            if end > i:
                body = text[i : end + 1]
                # The original regex to extract JSON objects. It is safe
                # here because the outer boundary is already fixed; the
                # regex only runs on the bounded substring.
                items = re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", body)
                if len(items) > 12:
                    head = ", ".join(items[:5])
                    tail = ", ".join(items[-2:])
                    out.append(f"[{head}, … ({len(items) - 7} more items omitted) …, {tail}]")
                    i = end + 1
                    continue
        out.append(text[i])
        i += 1
    return "".join(out)


_CODE_HINT_RE = re.compile(
    r"(?m)^\s*(?:def |class |async def |@\w|import |from \w+ import )",
)


def _looks_like_python(text: str) -> bool:
    """Cheap pre-filter before paying for an AST parse.

    Requires several structural lines rather than one, so a prose
    observation that merely quotes ``import x`` is not treated as source.
    """
    return len(_CODE_HINT_RE.findall(text)) >= 3


def _skeletonize_python(text: str, max_chars: int) -> str:
    """Replace function/method bodies with a one-line elision.

    Rationale: the hard cap slices at a byte offset, which lands
    mid-function and hands the model a syntactically broken fragment —
    worse than showing fewer functions completely. Keeping every
    signature (plus decorators, docstring first line, and the module's
    imports and class headers) preserves the structure a model needs to
    reason about a file, and drops the bodies that dominate the bytes.

    Bodies are kept while the budget allows, longest-last: short helpers
    usually carry the meaning, and a single huge function should not
    evict every signature after it.

    Returns ``text`` unchanged when it is not parseable Python or when
    the result would not actually be smaller, so the caller falls
    through to the existing hard cap.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return text

    lines = text.splitlines()

    # Collect body spans of every function, innermost first, so nested
    # helpers are elided before their enclosing function is considered.
    spans: list[tuple[int, int, int]] = []  # (start_line, end_line, size)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if not body:
            continue
        first = body[0]
        # Keep a leading docstring; it is the cheapest useful summary.
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            if len(body) == 1:
                continue
            first = body[1]
        # ``lineno`` on a decorated definition points at its ``def``, not at
        # the first decorator above it. Starting the elision there would cut
        # the ``def`` while leaving the decorators stranded, which does not
        # parse. Back up to the earliest decorator line.
        start = first.lineno
        for deco in getattr(first, "decorator_list", ()) or ():
            deco_line = getattr(deco, "lineno", None)
            if deco_line:
                start = min(start, deco_line)
        # The function node's own end_lineno already covers its whole body,
        # including nested definitions. Walking children would hit nodes
        # like ``arguments`` that carry no position at all.
        end = getattr(node, "end_lineno", None) or 0
        if end <= start:
            continue
        size = sum(len(lines[i]) + 1 for i in range(start - 1, min(end, len(lines))))
        spans.append((start, end, size))

    if not spans:
        return text

    # Elide the largest bodies first until we fit, so the budget buys many
    # small signatures rather than one long body. A span already inside an
    # elided one is skipped — its lines are gone either way, and counting
    # it again would over-estimate the savings and elide more than needed.
    #
    # Each elision costs its own marker line, so that is charged back
    # against the budget. Without it the loop stops early, the result
    # still exceeds max_chars, and the byte-offset cap runs anyway —
    # re-introducing the mid-function slice this pass exists to avoid.
    _marker_cost = 40
    elide: dict[int, int] = {}
    remaining = len(text)
    for start, end, size in sorted(spans, key=lambda s: -s[2]):
        if remaining <= max_chars:
            break
        if any(start >= s and end <= e for s, e in elide.items()):
            continue
        elide[start] = end
        remaining -= size - _marker_cost

    if not elide:
        return text
    out: list[str] = []
    skip_until = 0
    for idx, line in enumerate(lines, start=1):
        if idx <= skip_until:
            continue
        if idx in elide:
            end = elide[idx]
            indent = len(line) - len(line.lstrip())
            dropped = end - idx + 1
            out.append(f"{' ' * indent}...  # ({dropped} 行函数体已省略)")
            skip_until = end
            continue
        out.append(line)

    result = "\n".join(out)
    if len(result) > max_chars:
        # Eliding bodies was not enough: module-level code (imports,
        # constants, class bodies) can exceed the budget on its own. Finish
        # on a *line* boundary instead of leaving it to the byte-offset cap,
        # which would slice mid-statement and hand the model source that no
        # longer parses — the exact failure this pass exists to prevent.
        kept: list[str] = []
        used = 0
        for line in out:
            cost = len(line) + 1
            if used + cost > max_chars - 48:
                break
            kept.append(line)
            used += cost
        # Cutting at an arbitrary line can land inside a body — or inside a
        # multi-line signature — and produce source that no longer parses.
        # Character heuristics get this wrong (``def f(`` looks like a
        # top-level statement but leaves an unclosed paren), so rewind to
        # the last prefix that actually parses. Bounded to a few hundred
        # attempts; the parse is over a <=6KB string.
        for back in range(len(kept), max(0, len(kept) - 400), -1):
            try:
                ast.parse("\n".join(kept[:back]))
            except SyntaxError:
                continue
            del kept[back:]
            break
        # A prefix can parse and still end on dangling decorators: the
        # ``def`` they apply to was cut, and a bare ``@foo`` line is a
        # syntax error only once something follows it. Drop the orphans.
        while kept and kept[-1].lstrip().startswith("@"):
            kept.pop()
        while kept and not kept[-1].strip():
            kept.pop()
        dropped = len(out) - len(kept)
        if dropped > 0:
            kept.append(f"#  …({dropped} 行已省略 · 完整内容见 Journal)")
        result = "\n".join(kept)
    if text.endswith("\n"):
        result += "\n"
    return result if len(result) < len(text) else text


def _hard_cap(text: str, max_chars: int) -> str:
    """Keep head + tail when over budget. Errors and final results
    usually live in the tail; the head shows what tool ran. Middle
    is most often boilerplate."""
    if len(text) <= max_chars:
        return text
    head_n = max_chars * 2 // 3
    tail_n = max_chars - head_n - 32  # 32 chars for the marker
    head = text[:head_n]
    tail = text[-tail_n:] if tail_n > 0 else ""
    return f"{head}\n\n…(已压缩中段 {len(text) - head_n - tail_n} 字符)…\n\n{tail}"


def _hard_cap_parallel_sections(text: str, max_chars: int) -> str:
    """Cap each parallel-tool result independently so no receipt disappears."""

    matches = list(_PARALLEL_SECTION_HEADER_RE.finditer(text))
    if len(matches) < 2 or len(text) <= max_chars:
        return text
    prefix = text[: matches[0].start()].rstrip()
    separator_chars = 2 * (len(matches) - 1)
    available = max_chars - len(prefix) - separator_chars
    if available < len(matches) * 256:
        return text
    per_section = max(256, available // len(matches) - 32)
    sections: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append(_hard_cap(text[match.start() : end].rstrip(), per_section))
    joined = "\n\n".join(sections)
    return f"{prefix}\n{joined}".lstrip() if prefix else joined


def juice(
    text: str,
    *,
    max_chars: int = 6000,
    enable_html: bool = True,
    enable_url: bool = True,
    enable_dedup: bool = True,
    enable_array: bool = True,
    enable_code: bool = True,
    enable_cap: bool = True,
) -> tuple[str, JuiceStats]:
    """Apply the compression pipeline. Returns (compressed_text, stats).

    The protected-pattern guard runs *before* compression and is
    re-asserted after: if any sentinel disappeared we revert to the
    pre-compression text for that span. Cheaper than tracking spans
    surgically and is the right safety default — when in doubt,
    don't strip.
    """
    if not text:
        return text, JuiceStats(0, 0, ())
    before = len(text)
    passes: list[str] = []

    # Snapshot the protected substrings so we can verify nothing
    # got eaten. This is a correctness check, not a recovery step —
    # a juicer that ate `(工具失败)` would silently teach the model
    # the wrong thing about retry semantics.
    protected_snapshot = _PROTECTED_RE.findall(text)

    out = text
    if enable_html:
        new = _strip_html(out)
        if new != out:
            passes.append("html")
            out = new
    if enable_url:
        new = _shorten_long_urls(out)
        if new != out:
            passes.append("url")
            out = new
    if enable_array:
        new = _trim_oversized_arrays(out)
        if new != out:
            passes.append("array")
            out = new
    if enable_dedup:
        new = _dedup_repeated_lines(out)
        if new != out:
            passes.append("dedup")
            out = new
    if enable_code and len(out) > max_chars and _looks_like_python(out):
        # Try structural trimming before the byte-offset cap: eliding whole
        # function bodies keeps the file parseable and every signature
        # visible, where the cap would slice mid-function.
        new = _skeletonize_python(out, max_chars)
        if new != out:
            passes.append("code")
            out = new
    if enable_cap and len(out) > max_chars:
        new = _hard_cap_parallel_sections(out, max_chars)
        if new != out:
            passes.append("parallel-cap")
            out = new
        new = _hard_cap(out, max_chars)
        if new != out:
            passes.append("cap")
            out = new

    # Re-verify protected sentinels. If any were lost (most likely
    # by the hard cap eating the tail), restore the original — we'd
    # rather pay tokens than mislead the loop.
    if protected_snapshot:
        post_snapshot = _PROTECTED_RE.findall(out)
        if len(post_snapshot) < len(protected_snapshot):
            return text, JuiceStats(before, before, ())

    return out, JuiceStats(
        before=before,
        after=len(out),
        passes=tuple(passes),
    )


def is_enabled() -> bool:
    """Feature flag. Default ON — compression has been validated to
    reduce token usage without losing sentinel patterns. The protected-
    pattern guard re-verifies that critical sentinels like `(工具失败)`
    and `[1/N]` headers survive every pass, reverting to the raw text
    if any disappeared.

    Opt out by setting ECHO_TOKEN_JUICE to "0", "false", "no", or
    "off". Any other value (including unset) keeps compression on."""
    raw = os.environ.get("ECHO_TOKEN_JUICE", "").strip().lower()
    return raw not in {"0", "false", "no", "off"}
