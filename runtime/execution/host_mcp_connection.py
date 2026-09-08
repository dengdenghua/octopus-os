"""Connection coordinates shared with engines without loading an MCP server."""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class HostMCPConnection:
    url: str
    token: str = field(repr=False)

    def __post_init__(self) -> None:
        address = urlsplit(self.url)
        if (
            address.scheme != "http"
            or address.hostname != "127.0.0.1"
            or address.port is None
            or address.port <= 0
            or address.username is not None
            or address.password is not None
            or address.path != "/mcp"
            or address.query
            or address.fragment
        ):
            raise ValueError("host MCP connection must be a loopback endpoint")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", self.token):
            raise ValueError("host MCP token is invalid")
