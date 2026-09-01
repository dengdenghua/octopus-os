"""
Constitution profiles · strict / normal / lax.

Documented in ``docs/constitution.md §8``. Profile selects how
aggressively the gate enforces each layer:

* ``strict`` (default) · every Rule hit rewrites-or-blocks ·
  every Judge hit is authoritative (block / human_gate stick).
  Suitable for production agents talking to external channels.

* ``normal`` · Rule hits still enforced (PII rewritten · secrets
  blocked) · Judge verdicts downgrade from block → journal-only
  (verdict becomes ``allow`` but ``reason`` records the would-be
  block). Suitable for local-IDE / personal-dev use where the
  LLM judge cost isn't worth a false-positive that interrupts
  flow.

* ``lax`` · Only the hard-floor rules fire: secret leaks still
  block (can't un-ring that bell) · PII is allowed (no rewrite)
  · Judge is entirely audit-only. Suitable for air-gapped /
  trusted-network deployments where the operator accepts the
  risk · the runtime still records everything it would have
  caught.

Profile is a **process-level** setting via ``set_profile()``.
Per-agent or per-session overrides can be added later (the
gate accepts a ``profile`` kwarg that falls back to the global
setting).
"""

from __future__ import annotations

from typing import Literal

from runtime.platform.process.service_provider import get_provider

ProfileName = Literal["strict", "normal", "lax"]

_VALID_PROFILES: tuple[ProfileName, ...] = ("strict", "normal", "lax")


def set_profile(name: ProfileName) -> None:
    """Install the global profile. Raises ``ValueError`` on
    unknown names · callers should surface that to the user
    rather than silently fall back."""
    if name not in _VALID_PROFILES:
        raise ValueError(f"unknown profile {name!r} · must be one of {_VALID_PROFILES}")
    get_provider().register_instance("constitution_profile", name)


def get_profile() -> ProfileName:
    return get_provider().get("constitution_profile", default="strict")


def reset_profile_for_tests() -> None:
    """Back to the default. Test fixtures call this between cases."""
    get_provider().unregister("constitution_profile")


# ═══════════════════════════════════════════════════════════
# Policy flags · gate consults these rather than switching on
# the profile name · keeps adding a new profile mechanical.
# ═══════════════════════════════════════════════════════════


def enforces_pii_rewrite(profile: ProfileName | None = None) -> bool:
    """True if the gate should rewrite PII to placeholders.
    False means PII passes through (still logged via violations
    list · just not mutated)."""
    p = profile or get_profile()
    return p in ("strict", "normal")


def enforces_judge_verdict(profile: ProfileName | None = None) -> bool:
    """True if the LLM judge's ``block`` / ``human_gate`` actions
    are authoritative. False means judge decisions degrade to
    ``allow`` but the reason still propagates for audit."""
    p = profile or get_profile()
    return p == "strict"


def enforces_secrets_block(profile: ProfileName | None = None) -> bool:
    """Secrets always block · even in lax. This is the hard
    floor · once a credential is on the wire the attacker owns
    it. Kept as a flag so the contract is explicit, not
    implicit-because-no-profile-touches-it."""
    return True


__all__ = [
    "ProfileName",
    "set_profile",
    "get_profile",
    "reset_profile_for_tests",
    "enforces_pii_rewrite",
    "enforces_judge_verdict",
    "enforces_secrets_block",
]
