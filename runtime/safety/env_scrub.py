"""runtime.safety.env_scrub · credential-scrubbing for unconfined subprocesses.

A subprocess that inherits the server's full ``os.environ`` can leak any
secret the server holds (``ANTHROPIC_API_KEY``, ``GH_TOKEN``, DB
passwords, …) the moment something echoes ``$VAR`` inside it. Two code
paths spawn such children:

  * model-driven ``exec`` on the compat-gateway path (no bound Session) —
    see ``execution.suckers.write_skills``;
  * the user-facing interactive terminal WebSocket — see
    ``sensing.gateway.terminal_router``.

Both share this single source of truth for "what looks like a credential"
so the two surfaces can never drift apart.
"""

from __future__ import annotations

import os
from typing import Any

# Env-var names whose value is almost always a credential. Matched
# case-insensitively as a substring, so ANTHROPIC_API_KEY, GH_TOKEN,
# DB_PASSWORD, AWS_SECRET_ACCESS_KEY, ``*_APIKEY`` … all match.
SENSITIVE_ENV_NAME_HINTS: tuple[str, ...] = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "PASSPHRASE",
    "CREDENTIAL",
    "APIKEY",
    "PRIVATE",
    "COOKIE",
    "SESSION",
)
# Benign names that contain a hint substring but are needed by real
# commands and carry no secret — keep them.
SENSITIVE_ENV_NAME_KEEP: frozenset[str] = frozenset({"SSH_AUTH_SOCK"})

_ENV_SECRET_DETECTOR: Any = None


def scrub_credential_env(overlay: dict[str, str] | None = None) -> dict[str, str]:
    """Return the environment for an UNCONFINED subprocess: ``os.environ``
    minus any entry whose *name* looks like a credential or whose *value*
    is detected as a secret by the shared ``Redactor``, with the explicit
    caller-supplied ``overlay`` applied verbatim on top.

    Benign vars (PATH, HOME, LANG, …) are preserved so commands still
    run. A caller that deliberately passes ``overlay={"X": "y"}`` still
    gets ``X`` — explicit intent wins over the name/value heuristics.
    """
    global _ENV_SECRET_DETECTOR
    if _ENV_SECRET_DETECTOR is None:
        from runtime.platform.observability.redactor import Redactor

        _ENV_SECRET_DETECTOR = Redactor(
            enabled_categories={"api_key", "aws_secret", "private_key", "jwt"}
        )
    safe: dict[str, str] = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if upper not in SENSITIVE_ENV_NAME_KEEP:
            if any(hint in upper for hint in SENSITIVE_ENV_NAME_HINTS):
                continue
            sval = str(value)
            if sval and _ENV_SECRET_DETECTOR.redact(sval) != sval:
                continue
        safe[name] = value
    if overlay:
        safe.update({str(k): str(v) for k, v in overlay.items()})
    return safe


__all__ = [
    "scrub_credential_env",
    "SENSITIVE_ENV_NAME_HINTS",
    "SENSITIVE_ENV_NAME_KEEP",
]
