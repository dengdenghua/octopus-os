"""Quick benchmark: how much does TokenJuice actually save on
realistic tool observations? Run from project root:

    python tests/integration_token_juice_benchmark.py

Reports char delta + a tokens-saved estimate for each fixture.
"""

from __future__ import annotations

from runtime.core.cerebrum.token_juicer import juice


def _approx_tokens(s: str) -> int:
    """Cheap char-to-token estimate; close enough for relative
    comparison. Real tokenizers give ~3.5-4 chars/token for English,
    ~1.7 for CJK; we average."""
    return max(1, len(s) // 3)


def _report(label: str, raw: str) -> None:
    out, stats = juice(raw)
    saved_pct = (1 - stats.ratio) * 100 if stats.before else 0
    print(
        f"\n{label}\n"
        f"  before: {stats.before:>6} chars (~{_approx_tokens(raw):>5} tok)\n"
        f"   after: {stats.after:>6} chars (~{_approx_tokens(out):>5} tok)\n"
        f"   saved: {stats.saved:>6} chars ({saved_pct:>5.1f}%)\n"
        f"  passes: {','.join(stats.passes) or 'none'}"
    )


def main() -> int:
    # Fixture 1: a typical web-fetch on a news article. HTML is the
    # dominant cost; useful body is < 5% of bytes.
    fetch_url_observation = (
        "(real tool execution succeeded) fetch_url\n"
        '{"url": "https://example.com/long?utm_source=' + "x" * 250 + '",'
        ' "html": "<html><head>'
        + "<script>window.dataLayer=[];function gtag(){}</script>" * 20
        + "<style>body{margin:0}</style>" * 10
        + "</head><body>"
        + "<nav>"
        + '<a href="/p">link</a>' * 30
        + "</nav>"
        + "<article><h1>Title</h1>"
        + "<p>Real article paragraph.</p>" * 8
        + "</article>"
        + "<footer>"
        + "<a>fl</a>" * 50
        + "</footer>"
        + '</body></html>"}'
    )
    _report("fetch_url HTML page", fetch_url_observation)

    # Fixture 2: grep_text result with many matches across a big
    # codebase (the kind that returns hundreds of identical-looking
    # filenames).
    grep_observation = "(real tool execution succeeded) grep_text\n"
    for i in range(60):
        grep_observation += f'{{"file": "src/x{i}.py", "line": 1, "text": "import os"}}\n'
    _report("grep_text repetitive matches", grep_observation)

    # Fixture 3: exec_shell with retry warnings spamming the buffer.
    spammy_shell = (
        "(real tool execution succeeded) exec_shell\n"
        '{"argv": ["pytest", "-q"], "exit_code": 0, "stdout": "'
        + ("warning: deprecated\\n" * 50)
        + "PASSED 1234 tests in 12.3s"
        + '"}'
    )
    _report("shell with repeated warnings", spammy_shell)

    # Fixture 4: short observation — should pass through.
    tiny = '(real tool execution succeeded) read_file\n{"content": "OK"}'
    _report("tiny read_file (should be no-op)", tiny)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
