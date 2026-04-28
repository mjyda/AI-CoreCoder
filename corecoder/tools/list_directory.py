"""List directory contents."""

from pathlib import Path

from .base import Tool
from .sandbox import SANDBOX_ROOT, ensure_within_sandbox


class ListDirectoryTool(Tool):
    name = "list_directory"
    description = "List files/directories under a path."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path (default sandbox root)"},
            "recursive": {"type": "boolean", "description": "Whether to recurse subdirectories"},
            "limit": {"type": "integer", "description": "Maximum entries to return (default 200)"},
        },
    }

    def execute(self, path: str = "", recursive: bool = False, limit: int = 200) -> str:
        try:
            path = path or str(SANDBOX_ROOT)
            base = Path(path).expanduser().resolve()
            denied = ensure_within_sandbox(base)
            if denied:
                return f"Error: {denied}"
            if not base.exists():
                return f"Error: {path} not found"
            if not base.is_dir():
                return f"Error: {path} is not a directory"

            limit = max(1, min(limit, 2000))
            entries = base.rglob("*") if recursive else base.iterdir()
            rows: list[str] = []
            for entry in entries:
                marker = "/" if entry.is_dir() else ""
                rows.append(str(entry) + marker)
                if len(rows) >= limit:
                    break
            return "\n".join(rows) if rows else "(empty directory)"
        except Exception as e:
            return f"Error: {e}"
