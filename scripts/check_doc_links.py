#!/usr/bin/env python3
"""Check for broken internal links in markdown documentation."""

import re
import sys
from pathlib import Path


def find_md_links(content: str) -> list[tuple[str, int]]:
    """Extract markdown links with line numbers."""
    links = []
    fence: str | None = None
    for i, line in enumerate(content.splitlines(), 1):
        fence_match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker[0]
            elif marker[0] == fence:
                fence = None
            continue
        if fence is not None:
            continue
        # Code examples frequently contain Markdown-shaped protocol syntax
        # (for example ``@[label](uri)``). They are not documentation links.
        visible_line = re.sub(r"(`+).*?\1", "", line)
        # Match [text](path) and [text](path#anchor)
        for match in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", visible_line):
            url = match.group(2)
            # Skip external links, anchors-only, and mailto
            if not url.startswith(("http://", "https://", "#", "mailto:")):
                links.append((url, i))
    return links


def resolve_link(source_file: Path, link: str) -> Path | None:
    """Resolve relative link from source file."""
    # Remove anchor
    path_part = link.split("#")[0]
    if not path_part:
        return None

    # Resolve relative to source file's directory
    return (source_file.parent / path_part).resolve()


def main() -> int:
    """Check all markdown files for broken links."""
    repo_root = Path(__file__).parent.parent
    docs_dir = repo_root / "docs"

    if not docs_dir.exists():
        print(f"Error: {docs_dir} not found")
        return 1

    broken_links = []

    for md_file in docs_dir.rglob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        links = find_md_links(content)

        for link, line_num in links:
            target = resolve_link(md_file, link)
            if target is None:
                continue

            if not target.exists():
                rel_source = md_file.relative_to(repo_root)
                broken_links.append((rel_source, line_num, link, target))

    if broken_links:
        print(f"Found {len(broken_links)} broken links:\n")
        for source, line_num, link, target in sorted(broken_links):
            print(f"{source}:{line_num}")
            print(f"  Link: {link}")
            print(f"  Target: {target}")
            print()
        return 1
    print("All internal links are valid ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

