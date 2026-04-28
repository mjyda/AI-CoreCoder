"""Backward-compatible re-export for platform MCP registry."""

from .platform.mcp import DEFAULT_MCP_SERVERS, MCPRegistry, MCPServerSpec

__all__ = ["MCPServerSpec", "DEFAULT_MCP_SERVERS", "MCPRegistry"]
