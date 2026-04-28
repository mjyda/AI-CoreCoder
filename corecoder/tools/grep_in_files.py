"""Search text content in files (alias-friendly wrapper)."""

from .base import Tool
from .grep import GrepTool
from .sandbox import SANDBOX_ROOT


class GrepInFilesTool(Tool):
    name = "grep_in_files"
    description = "Search file contents with regex pattern."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Regex or plain text pattern"},
            "path": {"type": "string", "description": "File/dir to search"},
            "include": {"type": "string", "description": "Glob filter, e.g. *.md"},
        },
        "required": ["query"],
    }

    def execute(self, query: str, path: str = "", include: str | None = None) -> str:
        path = path or str(SANDBOX_ROOT)
        return GrepTool().execute(pattern=query, path=path, include=include)
