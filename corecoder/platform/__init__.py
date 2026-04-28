"""Platform-level modules: configuration and MCP registry."""

from .config import Config
from .mcp import MCPRegistry, MCPServerSpec, DEFAULT_MCP_SERVERS

__all__ = ["Config", "MCPRegistry", "MCPServerSpec", "DEFAULT_MCP_SERVERS"]
