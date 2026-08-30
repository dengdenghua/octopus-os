"""Version-isolation boundary for Echo OS integrations with Echo Agent.

Modules are intentionally not re-exported here. Each OS feature imports only
its own adapter so an incompatible optional Agent subsystem cannot prevent
unrelated appliance services from starting.
"""

__all__: list[str] = []
