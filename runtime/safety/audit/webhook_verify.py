"""Webhook HMAC verifier — Stripe/GitHub-style signature check.

Inbound webhooks must be authenticated; otherwise any attacker who
knows the endpoint URL can forge events (fake Stripe charges,
phantom GitHub pushes, synthetic Shopify orders, …). The industry
pattern is HMAC-SHA256 over the raw request body, sent in a header
like ``X-Hub-Signature-256`` (GitHub) or ``Stripe-Signature``.

This module ships with **presets for the common providers** and a
generic verifier you can point at any HMAC-based scheme.

Built-in presets
----------------
* **GitHub** (``X-Hub-Signature-256``) — value ``sha256=<hex>``
* **Stripe** (``Stripe-Signature``) — value ``t=<ts>,v1=<hex>,...``;
  includes replay-window + timestamp check
* **Shopify** (``X-Shopify-Hmac-SHA256``) — value is plain base64

Replay protection
-----------------
When the provider's signature includes a timestamp (Stripe), the
verifier rejects requests outside a configurable tolerance window
(default ±5 minutes). This blocks an attacker who captured a valid
signed request from replaying it days later.

Constant-time comparison
------------------------
``hmac.compare_digest`` is used everywhere so signature validation
can't be timing-sniffed.

Usage
-----

    from runtime.safety.audit.webhook_verify import (
        verify_github_signature,
        WebhookVerificationError,
    )

    try:
        verify_github_signature(
            secret=b"env:GH_WEBHOOK_SECRET",
            body=raw_bytes,
            signature_header=request.headers["x-hub-signature-256"],
        )
    except WebhookVerificationError as exc:
        return Response(status_code=401, body=str(exc))

FastAPI middleware
------------------
``create_verify_dependency(scheme, secret_getter)`` returns a FastAPI
dependency that rejects unsigned / invalid requests before they reach
the handler.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from collections.abc import Callable
from typing import Any, Literal

_LOG = logging.getLogger("echo.safety.webhook_verify")

# ── Constants ────────────────────────────────────────────────

DEFAULT_REPLAY_WINDOW_SECONDS: float = 300.0  # 5 minutes

SchemeName = Literal["github", "stripe", "shopify", "generic"]


class WebhookVerificationError(Exception):
    """Raised when a webhook signature is missing, malformed, or wrong.

    The message is safe to surface in an HTTP 401 body — it doesn't
    leak the expected signature value.
    """


# ── GitHub ───────────────────────────────────────────────────


def verify_github_signature(
    *,
    secret: bytes,
    body: bytes,
    signature_header: str | None,
) -> None:
    """Verify GitHub's ``X-Hub-Signature-256`` header.

    Format: ``sha256=<hex>``. Raises ``WebhookVerificationError`` on
    any mismatch.
    """
    if not signature_header:
        raise WebhookVerificationError("missing X-Hub-Signature-256 header")
    if not signature_header.startswith("sha256="):
        raise WebhookVerificationError("unexpected signature prefix")
    provided = signature_header[len("sha256=") :]
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise WebhookVerificationError("signature mismatch")


# ── Shopify ──────────────────────────────────────────────────


def verify_shopify_signature(
    *,
    secret: bytes,
    body: bytes,
    signature_header: str | None,
) -> None:
    """Verify Shopify's ``X-Shopify-Hmac-SHA256`` header (base64).

    Raises ``WebhookVerificationError`` on mismatch.
    """
    if not signature_header:
        raise WebhookVerificationError("missing X-Shopify-Hmac-SHA256 header")
    expected = base64.b64encode(hmac.new(secret, body, hashlib.sha256).digest()).decode("ascii")
    if not hmac.compare_digest(expected, signature_header.strip()):
        raise WebhookVerificationError("signature mismatch")


# ── Stripe ───────────────────────────────────────────────────


def verify_stripe_signature(
    *,
    secret: bytes,
    body: bytes,
    signature_header: str | None,
    tolerance_seconds: float = DEFAULT_REPLAY_WINDOW_SECONDS,
    now: float | None = None,
) -> None:
    """Verify Stripe's ``Stripe-Signature`` header with replay check.

    Format: ``t=<unix>,v1=<hex>[,v1=<hex>...]``. Accepts the request
    if at least one ``v1`` matches AND the timestamp is within
    ``tolerance_seconds`` of ``now``.
    """
    if not signature_header:
        raise WebhookVerificationError("missing Stripe-Signature header")

    items = {}
    for piece in signature_header.split(","):
        if "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        items.setdefault(k.strip(), []).append(v.strip())

    ts_values = items.get("t") or []
    v1_values = items.get("v1") or []
    if not ts_values or not v1_values:
        raise WebhookVerificationError("malformed Stripe signature header")

    try:
        ts = int(ts_values[0])
    except ValueError:
        raise WebhookVerificationError("malformed timestamp") from None

    current = now if now is not None else time.time()
    if abs(current - ts) > tolerance_seconds:
        raise WebhookVerificationError(
            f"timestamp outside tolerance window ({tolerance_seconds}s)",
        )

    signed_payload = f"{ts}.".encode() + body
    expected = hmac.new(secret, signed_payload, hashlib.sha256).hexdigest()

    for provided in v1_values:
        if hmac.compare_digest(expected, provided):
            return
    raise WebhookVerificationError("signature mismatch")


# ── Generic verifier ─────────────────────────────────────────


def verify_generic_hmac(
    *,
    secret: bytes,
    body: bytes,
    provided_hex: str | None,
    algo: str = "sha256",
    prefix: str = "",
) -> None:
    """Generic ``HMAC-<algo>`` verifier.

    Works for any provider whose scheme is ``hmac(body, secret)`` in
    hex, optionally with a prefix like ``sha256=``.
    """
    if not provided_hex:
        raise WebhookVerificationError("missing signature")
    if prefix and provided_hex.startswith(prefix):
        provided_hex = provided_hex[len(prefix) :]
    try:
        hash_fn = getattr(hashlib, algo)
    except AttributeError:
        raise WebhookVerificationError(f"unknown algo {algo!r}") from None
    expected = hmac.new(secret, body, hash_fn).hexdigest()
    if not hmac.compare_digest(expected, provided_hex):
        raise WebhookVerificationError("signature mismatch")


# ── Dispatcher ───────────────────────────────────────────────


def verify_webhook(
    scheme: SchemeName,
    *,
    secret: bytes,
    body: bytes,
    headers: dict[str, str],
    **extra: Any,
) -> None:
    """Pick the right verifier based on ``scheme`` and dispatch.

    ``headers`` should be lower-cased keys (FastAPI's ``request.headers``
    is lowercased by default).
    """
    if scheme == "github":
        verify_github_signature(
            secret=secret,
            body=body,
            signature_header=headers.get("x-hub-signature-256"),
        )
    elif scheme == "stripe":
        verify_stripe_signature(
            secret=secret,
            body=body,
            signature_header=headers.get("stripe-signature"),
            tolerance_seconds=float(
                extra.get("tolerance_seconds", DEFAULT_REPLAY_WINDOW_SECONDS),
            ),
            now=extra.get("now"),
        )
    elif scheme == "shopify":
        verify_shopify_signature(
            secret=secret,
            body=body,
            signature_header=headers.get("x-shopify-hmac-sha256"),
        )
    elif scheme == "generic":
        verify_generic_hmac(
            secret=secret,
            body=body,
            provided_hex=headers.get(
                str(extra.get("header_name", "x-signature")).lower(),
            ),
            algo=str(extra.get("algo", "sha256")),
            prefix=str(extra.get("prefix", "")),
        )
    else:
        raise ValueError(f"unknown scheme {scheme!r}")


# ── FastAPI dependency ───────────────────────────────────────


def create_verify_dependency(
    scheme: SchemeName,
    *,
    secret_getter: Callable[[], bytes],
    **extra: Any,
) -> Callable[..., Any]:
    """Return a FastAPI dependency that verifies inbound webhooks.

    ``secret_getter`` is called per request so secrets can come from a
    vault without being pinned at import time.

    Usage::

        verify_dep = create_verify_dependency(
            "github",
            secret_getter=lambda: os.environ["GH_WEBHOOK_SECRET"].encode(),
        )

        @router.post("/webhooks/github", dependencies=[Depends(verify_dep)])
        async def handle(...): ...
    """
    # Import at factory time so FastAPI sees the proper Request
    # annotation when introspecting the dependency function.
    try:
        from fastapi import HTTPException, Request
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("fastapi required for create_verify_dependency") from exc

    # Build the dependency with a properly annotated signature so
    # FastAPI's DI doesn't mistake ``request`` for a query parameter.
    async def dependency(request: Request) -> None:
        body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            verify_webhook(
                scheme,
                secret=secret_getter(),
                body=body,
                headers=headers,
                **extra,
            )
        except WebhookVerificationError as err:
            _LOG.warning("webhook rejected (%s): %s", scheme, err)
            raise HTTPException(status_code=401, detail=str(err)) from err

    # Re-bind annotation explicitly — Python sometimes loses the
    # Request annotation when the function is closure-captured.
    dependency.__annotations__["request"] = Request
    return dependency


__all__ = [
    "DEFAULT_REPLAY_WINDOW_SECONDS",
    "SchemeName",
    "WebhookVerificationError",
    "create_verify_dependency",
    "verify_generic_hmac",
    "verify_github_signature",
    "verify_shopify_signature",
    "verify_stripe_signature",
    "verify_webhook",
]
