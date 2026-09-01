"""Embeddable Agent kernel facade.

The kernel package is the integration boundary between Echo's reasoning
runtime and any host application.  Hosts should depend on :class:`AgentKernel`
instead of assembling Cerebrum, the execution stack, and persistence by hand.
"""

from .kernel import AgentKernel

__all__ = ["AgentKernel"]
