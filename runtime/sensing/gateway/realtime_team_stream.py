"""Multi-agent team-topology stream driver for the realtime runtime.

Split out of ``realtime_cerebrum.py``: resolve a ``topology_id`` through
the organization registry, run ``TeamRunner`` on a worker thread, and
bridge its live events (role text, sub-tool calls, subagent lifecycle,
heartbeats) onto the same ``item/*`` notifications the ReAct path uses.
Falls back to single-agent ReAct when the topology id is stale.

The implementations live in sibling submodules (``_team_stream_topology``,
``_team_stream_group_fanout``, ``_realtime_team_stream_mesh``); this module
re-exports them so the public API surface is unchanged.
"""

from __future__ import annotations

from runtime.sensing.gateway._realtime_team_stream_mesh import (
    _budget_for_graph,
    _drive_swarm_mesh,
    _graph_favors_mesh,
)
from runtime.sensing.gateway._team_stream_group_fanout import _drive_group_fanout
from runtime.sensing.gateway._team_stream_topology import _drive_team_topology
from runtime.sensing.gateway.realtime_approval import GatewayApprovalProvider

__all__ = [
    "GatewayApprovalProvider",
    "_drive_group_fanout",
    "_drive_swarm_mesh",
    "_drive_team_topology",
    "_graph_favors_mesh",
    "_budget_for_graph",
]
