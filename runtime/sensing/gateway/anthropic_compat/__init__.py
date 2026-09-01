"""Anthropic Managed Agents compatibility layer.

Exposes ``/v1/sessions`` REST + SSE endpoints that speak the same
protocol as Anthropic's hosted Managed Agents service, so the official
``anthropic`` Python/TS SDK (``client.beta.sessions.*``) can connect
to echo-agent as a self-hosted backend.

Usage::

    from runtime.sensing.gateway.anthropic_compat import create_anthropic_compat_router

    router = create_anthropic_compat_router(stack=stack, ...)
    app.include_router(router)
"""

from .router import create_anthropic_compat_router

__all__ = ["create_anthropic_compat_router"]
