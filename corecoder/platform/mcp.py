"""MCP abstraction layer for CoreCoder.

This module provides a lightweight, project-friendly MCP adapter:
- Each MCP server is represented as a capability group.
- Capability groups are mapped to existing CoreCoder tools for now.
- The mapping can be replaced by real remote MCP clients later.
"""

from dataclasses import dataclass

from ..tools import ALL_TOOLS
from ..tools.base import Tool


@dataclass(frozen=True)
class MCPServerSpec:
    """A minimal MCP server definition."""

    name: str
    capability: str
    tool_names: tuple[str, ...]


DEFAULT_MCP_SERVERS: tuple[MCPServerSpec, ...] = (
    MCPServerSpec(
        name="browser-history-mcp",
        capability="browser-history",
        tool_names=("browser_history", "fetch_url_content"),
    ),
    MCPServerSpec(
        name="filesystem-mcp",
        capability="filesystem",
        tool_names=(
            "list_directory",
            "read_file",
            "write_file",
            "append_file",
            "edit_file",
            "search_files",
            "grep_in_files",
            "file_info",
            "glob",
            "grep",
        ),
    ),
    MCPServerSpec(
        name="shell-mcp",
        capability="shell",
        tool_names=("bash",),
    ),
    MCPServerSpec(
        name="code-search-mcp",
        capability="code-search",
        tool_names=("glob", "grep"),
    ),
    MCPServerSpec(
        name="notes-mcp",
        capability="notes",
        tool_names=("read_file", "write_file", "edit_file"),
    ),
    MCPServerSpec(
        name="mail-imap-mcp",
        capability="mail",
        tool_names=(
            "gmail_list_recent",
            "gmail_search",
            "gmail_get_content",
            "gmail_send_email",
            "gmail_reply_email",
            "gmail_delete_email",
            "gmail_mark_read",
            "gmail_move_to_trash",
        ),
    ),
)


class MCPRegistry:
    """Resolves MCP capabilities to concrete tool instances."""

    def __init__(self, servers: tuple[MCPServerSpec, ...] | None = None):
        self._servers = servers or DEFAULT_MCP_SERVERS
        self._tool_index = {tool.name: tool for tool in ALL_TOOLS}

    def available_capabilities(self) -> list[str]:
        return sorted({spec.capability for spec in self._servers})

    def tools_for_capabilities(self, capabilities: list[str]) -> list[Tool]:
        names: list[str] = []
        caps = set(capabilities)
        for spec in self._servers:
            if spec.capability in caps:
                names.extend(spec.tool_names)

        resolved: list[Tool] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            tool = self._tool_index.get(name)
            if tool is not None:
                resolved.append(tool)
                seen.add(name)
        return resolved
