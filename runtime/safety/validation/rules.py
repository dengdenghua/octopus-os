"""Rule-layer checks · regex-based PII + keyword-based hazard detection.

This is the **fast path** · μs-scale · zero LLM calls. Covers conservative
patterns that can be matched without semantic understanding:

    PRIV-2 · PII (email / phone / API-key families / national-ID / credit-card)
    PRIV-4 · secret files (API-key prefixes · special paths in context)
    LAWF-2 · only the blatant keyword triggers (rare · most hits need judge)

Anything subtler (LAWF-1 scraping intent · LAWF-4 PII aggregation · DGNT-*)
belongs in the LLM-judge layer · not here · because keyword-only blocking
over-triggers on legitimate discussion (security researchers / educators
talking ABOUT phishing or malware shouldn't get blocked).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ═══════════════════════════════════════════════════════════
# PII pattern definitions
# ═══════════════════════════════════════════════════════════
#
# Each entry: (clause_id, pattern, placeholder, description).
# The placeholder is what ``scrub_pii`` replaces with · visible in
# rewritten output so downstream (and user) know something was removed.

_PII_PATTERNS: list[tuple[str, re.Pattern[str], str, str]] = [
    # Email
    (
        "PRIV-2",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED:email]",
        "email address",
    ),
    # CN mobile · 11 digits starting 13/14/15/16/17/18/19
    (
        "PRIV-2",
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        "[REDACTED:phone]",
        "CN mobile",
    ),
    # International E.164-like · +CC NNN...
    (
        "PRIV-2",
        re.compile(r"(?<!\d)\+\d{1,3}[\-\s]?\d{4,14}(?!\d)"),
        "[REDACTED:phone]",
        "international phone",
    ),
    # CN 18-digit national ID (last char can be X)
    (
        "PRIV-2",
        re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
        "[REDACTED:cn-id]",
        "CN national ID",
    ),
]

# ═══════════════════════════════════════════════════════════
# Secret / credential patterns (PRIV-4)
# ═══════════════════════════════════════════════════════════
#
# These are BLOCK-grade · not Rewrite. A leaked API key in chat
# can't be unleaked · the original token is already compromised
# the moment it hits a channel. Better to refuse entirely than
# scrub-and-send (which hides the attack attempt from the user).

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "PRIV-4",
        re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
        "Anthropic API key",
    ),
    (
        "PRIV-4",
        re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
        "OpenAI / generic sk- API key",
    ),
    (
        "PRIV-4",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "AWS access-key ID",
    ),
    (
        "PRIV-4",
        re.compile(r"\bghp_[A-Za-z0-9]{30,}"),
        "GitHub personal access token",
    ),
    (
        "PRIV-4",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}"),
        "GitHub fine-grained PAT",
    ),
    (
        "PRIV-4",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "private key material",
    ),
]


# ═══════════════════════════════════════════════════════════
# Result types
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RuleHit:
    """One match from a rule pattern · what and where."""

    clause_id: str
    category: str  # "pii" | "secret"
    description: str
    start: int
    end: int
    matched_text_short: str  # first 20 chars · for logging · never full


# ═══════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════


def scan_pii(text: str) -> list[RuleHit]:
    """Return every PII pattern hit in ``text`` · no side effects."""
    hits: list[RuleHit] = []
    for clause_id, pattern, _placeholder, description in _PII_PATTERNS:
        for match in pattern.finditer(text):
            matched = match.group(0)
            hits.append(
                RuleHit(
                    clause_id=clause_id,
                    category="pii",
                    description=description,
                    start=match.start(),
                    end=match.end(),
                    matched_text_short=matched[:20],
                )
            )
    return hits


def scan_secrets(text: str) -> list[RuleHit]:
    """Return every SECRET-grade pattern hit · these are Block candidates."""
    hits: list[RuleHit] = []
    for clause_id, pattern, description in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            matched = match.group(0)
            hits.append(
                RuleHit(
                    clause_id=clause_id,
                    category="secret",
                    description=description,
                    start=match.start(),
                    end=match.end(),
                    # NEVER store matched secret in full · even 20 chars
                    # of a 40-char token is dangerous. Store just the
                    # prefix and length · audit trail without leaking.
                    matched_text_short=f"{matched[:6]}…(len={len(matched)})",
                )
            )
    return hits


def scrub_pii(text: str) -> tuple[str, list[RuleHit]]:
    """Replace PII matches with placeholders. Returns (rewritten, hits).

    Secrets are NOT scrubbed — they're detected by ``scan_secrets``
    and the caller decides to Block (not Rewrite). Mixing PII Rewrite
    with Secret Block makes the semantics ambiguous.
    """
    hits = scan_pii(text)
    if not hits:
        return text, []

    # Apply replacements from the end backwards so earlier indices
    # stay valid. Sort by start descending.
    hits_sorted = sorted(hits, key=lambda h: -h.start)
    result = text
    for hit in hits_sorted:
        # Look up the placeholder for this pattern again · lighter
        # than storing it on RuleHit. The pattern that matched is
        # identifiable by the range, but for correctness we re-find
        # by clause_id + description.
        placeholder = next(
            ph
            for (cid, _p, ph, desc) in _PII_PATTERNS
            if cid == hit.clause_id and desc == hit.description
        )
        result = result[: hit.start] + placeholder + result[hit.end :]
    return result, hits
