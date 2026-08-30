from __future__ import annotations

from scripts.check_doc_links import find_md_links


def test_find_md_links_ignores_protocol_syntax_inside_code() -> None:
    content = """Real [guide](guide.md).
Inline ``@[label](echo-session:<payload>)`` syntax.
```markdown
[example](missing.md)
```
"""

    assert find_md_links(content) == [("guide.md", 1)]

