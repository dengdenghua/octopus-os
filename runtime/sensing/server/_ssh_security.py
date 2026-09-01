"""Shared SSH algorithm policy for optional Paramiko transports."""

from __future__ import annotations


def paramiko_disabled_algorithms() -> dict[str, list[str]]:
    """Disable the legacy RSA/SHA-1 signature algorithm.

    Paramiko 5.0.0 removes RSA/SHA-1 support upstream. Keep the explicit
    deny-list as defense in depth against policy regressions or alternate
    compatible transports. Returning a fresh mapping avoids callers sharing
    mutable policy state with Paramiko.
    """
    return {"keys": ["ssh-rsa"], "pubkeys": ["ssh-rsa"]}


__all__ = ["paramiko_disabled_algorithms"]
