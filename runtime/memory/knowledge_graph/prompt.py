from __future__ import annotations

from .triple import Triple


def format_triples_for_prompt(
    triples: list[Triple],
    *,
    header: str = "RELATED FACTS (from knowledge graph):",
    max_triples: int = 15,
    max_total_chars: int = 1500,
    min_confidence: float = 0.5,
) -> str:
    if not triples:
        return ""
    filtered = [t for t in triples if t.confidence >= min_confidence]
    if not filtered:
        return ""

    filtered.sort(key=lambda t: -t.confidence)
    filtered = filtered[:max_triples]

    lines = [header]
    used = len(header)
    for t in filtered:
        subj = str(t.subject)[:60]
        pred = str(t.predicate)[:25]
        obj = str(t.object)[:80]
        line = f"  ({subj}, {pred}, {obj})  conf={t.confidence:.2f}"
        if used + len(line) > max_total_chars:
            lines.append(f"  ... ({len(filtered) - (len(lines) - 1)} more truncated)")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)
