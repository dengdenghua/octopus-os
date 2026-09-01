"""Journal event types for constitution violations."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from runtime.memory.journal.journal import JournalEvent


class ConstitutionViolationEvent(JournalEvent):
    """A gate call that detected one or more constitution clauses
    being violated. Recorded regardless of action (block / rewrite /
    audit) so downstream analysis can count attempt rates.

    **Original text is NEVER stored in the event** · only a hash
    and a short snippet (which itself passes through scrub_pii).
    Rationale: if we log the violation reason verbatim and the
    violation WAS a PII leak · we've just moved the leak from
    "outbound channel" to "journal". Journal is an append-only log
    · much harder to redact after-the-fact.
    """

    event_type: Literal["constitution_violation"] = "constitution_violation"
    clause_ids: list[str] = Field(default_factory=list)
    action: Literal["block", "rewrite", "audit", "human_gate", "allow"] = "audit"
    destination: str = ""
    original_text_hash: str = ""  # sha256 of the input
    sanitized_text_hash: str | None = None
    violation_category: str = ""  # "pii" | "secret" | "malicious" | ...
    # One-line reason · already PII-scrubbed · for human audit
    reason: str = ""
