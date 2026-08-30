"""PII Redactor — regex-based secret/PII scrubbing.

What gets scrubbed
------------------
Categories detected out of the box:

* ``api_key`` — Anthropic / OpenAI / AWS / GCP / generic high-entropy tokens
* ``aws_secret`` — AWS secret access keys
* ``credit_card`` — 13-19 digit sequences passing Luhn
* ``email`` — RFC-5322-ish email addresses
* ``phone`` — e.g. +86 / +1 / E.164 / (XXX) XXX-XXXX
* ``ssn`` — US SSN (XXX-XX-XXXX)
* ``jwt`` — three base64url segments separated by dots
* ``private_key`` — PEM-encoded ``-----BEGIN … PRIVATE KEY-----`` blocks
* ``ipv4`` — IPv4 addresses (OPT-IN, off by default — some agents legitimately log IPs)

Usage
-----

    from runtime.platform.observability.redactor import redact_text, Redactor

    # Quick one-shot.
    safe = redact_text("My key is sk-ant-api03-abc123...")
    # → "My key is [REDACTED:api_key]"

    # Fine-grained control.
    r = Redactor(enabled_categories={"api_key", "credit_card"})
    safe, hits = r.redact_with_report("...")
    # hits = [{"category": "api_key", "count": 1}, ...]

Design
------
* Regex-only — no ML, no external deps. ``re.sub`` is ~100× faster than
  anything ML-based and is what production incident-response systems
  (e.g. Datadog, Splunk) default to for high-volume log pipelines.
* False-positive tolerant — a credit-card match that fails Luhn is
  rejected so a benign 16-digit order number doesn't get redacted.
* Deterministic + idempotent — redacting an already-redacted string
  is a no-op, so upstream logs and downstream logs stay consistent.
* Extensible — callers can register custom patterns via
  ``Redactor.add_pattern(category, regex)``.

Where to wire it
----------------
Three common attachment points:

1. Journal write path — ``FileOpEvent.content``, ``StepEvent.args``,
   ``SubToolEndEvent.output_preview`` before hitting disk.
2. Log formatter — the structured-logging handler (Round 18 #3).
3. LLM prompt composer — ``ContextComposer._render_*`` so a pasted
   secret in a recent trajectory doesn't leak into the next turn.

None of these integrations are wired by this module — it's a pure
utility. Call sites opt in so existing behaviour is unchanged.
"""

from __future__ import annotations

import re
from typing import Any

# ═══════════════════════════════════════════════════════════════
# Built-in patterns
# ═══════════════════════════════════════════════════════════════
# Ordered roughly most-specific-first so overlapping matches resolve
# cleanly (e.g. a JWT inside a PEM block should match the PEM block
# entirely, not its base64 fragments).

_BUILTIN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
            re.IGNORECASE,
        ),
    ),
    (
        "api_key",
        re.compile(
            # Anthropic: sk-ant-api03-...
            r"\bsk-ant-api\d{2}-[A-Za-z0-9_\-]{32,}\b"
            # Generic OpenAI-compatible provider keys, including
            # provider-prefixed domestic keys such as ``sk-kimi-...``.
            r"|\bsk-[A-Za-z0-9_\-]{20,}\b"
            # OpenAI new project / service-account / admin keys
            # (sk-proj-, sk-svcacct-, sk-admin-) — char class must
            # include _ and - because the body uses them.
            r"|\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}\b"
            # AWS access key id
            r"|\bAKIA[0-9A-Z]{16}\b"
            # Google Cloud service account
            r"|\bya29\.[0-9A-Za-z_\-]{20,}\b"
            # Google API key (AIza…)
            r"|\bAIza[0-9A-Za-z_\-]{35}\b"
            # GitHub PAT (ghp_)
            r"|\bghp_[A-Za-z0-9]{36}\b"
            # GitHub fine-grained token
            r"|\bgithub_pat_[A-Za-z0-9_]{80,}\b"
            # Stripe live / test secret
            r"|\bsk_(?:live|test)_[A-Za-z0-9]{20,}\b"
            # Stripe restricted
            r"|\brk_(?:live|test)_[A-Za-z0-9]{20,}\b"
            # Slack bot / user / app tokens
            r"|\bxox[bpasr]-[A-Za-z0-9\-]{10,}\b"
            # Twilio account SID / auth token are 32-hex; account SIDs
            # start with AC. Match the auth-token context to avoid
            # generic hex strings.
            r"|\bAC[0-9a-f]{32}\b"
            # SendGrid API key
            r"|\bSG\.[A-Za-z0-9_\-]{22,}\.[A-Za-z0-9_\-]{22,}\b",
        ),
    ),
    (
        "aws_secret",
        # Exactly 40 chars of base64 — aligned with AWS's format.
        # Two patterns: (a) the historical context-word form, (b) a
        # bare 40-char base64 immediately after the word ``secret`` or
        # adjacent to an access-key id.
        re.compile(
            r"(?i)aws[_-]?secret[_-]?(?:access[_-]?)?key"
            r"\s*[:=]\s*['\"]?([A-Za-z0-9/+]{40})['\"]?"
            r"|\bsecret(?:[_-]?access[_-]?)?key\s*[:=]\s*['\"]?([A-Za-z0-9/+]{40})['\"]?"
        ),
    ),
    (
        "jwt",
        # Three URL-safe-base64 segments separated by dots; the
        # signature segment may be empty for alg=none tokens (still
        # leaks claims). First segment must look like a JOSE header
        # (eyJ...) to avoid matching arbitrary base64 dotted strings.
        re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-=]{0,}"),
    ),
    (
        "email",
        re.compile(
            r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
        ),
    ),
    (
        "ssn",
        # US SSN (exactly XXX-XX-XXXX). Too noisy without the dashes.
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    (
        "phone",
        # E.164 (+XX...) or common US shapes.
        re.compile(
            r"\+?\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4}"
            r"|\(\d{3}\)\s?\d{3}-\d{4}"
        ),
    ),
    (
        "credit_card",
        # Loose 13-19 digit with optional separators — validated via Luhn below.
        re.compile(r"\b(?:\d[\s\-]?){13,19}\b"),
    ),
    (
        "ipv4",
        re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
        ),
    ),
]

# Categories enabled by default. ``ipv4`` is off — many agents log
# IPs for legitimate reasons and redacting them breaks debugging.
DEFAULT_CATEGORIES: frozenset[str] = frozenset(
    {
        "private_key",
        "api_key",
        "aws_secret",
        "jwt",
        "email",
        "ssn",
        "phone",
        "credit_card",
    }
)


# ═══════════════════════════════════════════════════════════════
# Luhn check for credit-card validation
# ═══════════════════════════════════════════════════════════════


def _passes_luhn(digits: str) -> bool:
    digits = re.sub(r"\D", "", digits)
    if not (13 <= len(digits) <= 19):
        return False
    total = 0
    alt = False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


# ═══════════════════════════════════════════════════════════════
# Redactor
# ═══════════════════════════════════════════════════════════════


class Redactor:
    """Regex-based secret/PII redactor.

    Parameters
    ----------
    enabled_categories:
        Which categories to scan for. Defaults to ``DEFAULT_CATEGORIES``
        (everything except ``ipv4``).
    replacement_format:
        Python format string with ``{category}`` placeholder. Default
        ``"[REDACTED:{category}]"`` keeps the category visible so
        developers can tell email-leaks from key-leaks in logs.
    """

    def __init__(
        self,
        *,
        enabled_categories: set[str] | frozenset[str] | None = None,
        replacement_format: str = "[REDACTED:{category}]",
    ) -> None:
        self._enabled = set(enabled_categories) if enabled_categories else set(DEFAULT_CATEGORIES)
        self._fmt = replacement_format
        # Category → list of patterns (so users can add custom patterns).
        self._patterns: dict[str, list[re.Pattern[str]]] = {}
        for cat, pat in _BUILTIN_PATTERNS:
            self._patterns.setdefault(cat, []).append(pat)

    # ── public API ──────────────────────────────────────────

    def add_pattern(self, category: str, pattern: str | re.Pattern[str]) -> None:
        """Register a custom redaction pattern.

        The category is auto-enabled. Accepts a raw string or a
        pre-compiled ``re.Pattern``.
        """
        compiled = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)
        self._patterns.setdefault(category, []).append(compiled)
        self._enabled.add(category)

    def enable(self, category: str) -> None:
        self._enabled.add(category)

    def disable(self, category: str) -> None:
        self._enabled.discard(category)

    def redact(self, text: str) -> str:
        """Return ``text`` with all enabled PII categories replaced."""
        if not text or not isinstance(text, str):
            return text
        out = text
        for category, patterns in self._patterns.items():
            if category not in self._enabled:
                continue
            replacement = self._fmt.format(category=category)
            for pat in patterns:
                if category == "credit_card":
                    # Only replace matches that pass Luhn.
                    out = pat.sub(
                        lambda m, r=replacement: r if _passes_luhn(m.group(0)) else m.group(0),
                        out,
                    )
                elif category == "aws_secret":
                    # The capture group is the secret; preserve the
                    # ``aws_secret_key=`` prefix so the log stays readable.
                    def _sub(m: re.Match[str], r: str = replacement) -> str:
                        start = m.start(1) - m.start(0)
                        end = m.end(1) - m.start(0)
                        return m.group(0)[:start] + r + m.group(0)[end:]

                    out = pat.sub(_sub, out)
                else:
                    out = pat.sub(replacement, out)
        return out

    def redact_with_report(self, text: str) -> tuple[str, list[dict[str, Any]]]:
        """Redact and return a per-category hit count.

        Report shape::

            [{"category": "api_key", "count": 2},
             {"category": "email",   "count": 1}]
        """
        if not text or not isinstance(text, str):
            return text, []
        counts: dict[str, int] = {}
        out = text
        for category, patterns in self._patterns.items():
            if category not in self._enabled:
                continue
            replacement = self._fmt.format(category=category)
            for pat in patterns:
                if category == "credit_card":

                    def _sub_cc(m: re.Match[str], cat: str = category, r: str = replacement) -> str:
                        if _passes_luhn(m.group(0)):
                            counts[cat] = counts.get(cat, 0) + 1
                            return r
                        return m.group(0)

                    out = pat.sub(_sub_cc, out)
                elif category == "aws_secret":

                    def _sub_aws(
                        m: re.Match[str], cat: str = category, r: str = replacement
                    ) -> str:
                        counts[cat] = counts.get(cat, 0) + 1
                        start = m.start(1) - m.start(0)
                        end = m.end(1) - m.start(0)
                        return m.group(0)[:start] + r + m.group(0)[end:]

                    out = pat.sub(_sub_aws, out)
                else:

                    def _sub(m: re.Match[str], cat: str = category, r: str = replacement) -> str:
                        counts[cat] = counts.get(cat, 0) + 1
                        return r

                    out = pat.sub(_sub, out)
        report = [{"category": c, "count": n} for c, n in sorted(counts.items())]
        return out, report

    def redact_dict(self, data: Any) -> Any:
        """Recursively redact strings inside a dict / list / tuple.

        Non-string leaves (numbers, bools, None) pass through.
        Useful for scrubbing JSON payloads before logging them.
        """
        if isinstance(data, str):
            return self.redact(data)
        if isinstance(data, dict):
            return {k: self.redact_dict(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.redact_dict(v) for v in data]
        if isinstance(data, tuple):
            return tuple(self.redact_dict(v) for v in data)
        return data


# ═══════════════════════════════════════════════════════════════
# Module-level convenience
# ═══════════════════════════════════════════════════════════════


_DEFAULT_REDACTOR = Redactor()


def redact_text(text: str) -> str:
    """Redact using the module-level default redactor."""
    return _DEFAULT_REDACTOR.redact(text)


def redact_dict(data: Any) -> Any:
    """Recursively redact using the module-level default redactor."""
    return _DEFAULT_REDACTOR.redact_dict(data)


__all__ = [
    "DEFAULT_CATEGORIES",
    "Redactor",
    "redact_dict",
    "redact_text",
]
