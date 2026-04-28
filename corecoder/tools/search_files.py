"""Search files by name pattern/keyword."""

from pathlib import Path

from .base import Tool
from .sandbox import SANDBOX_ROOT, ensure_within_sandbox


class SearchFilesTool(Tool):
    name = "search_files"
    description = "Search files by glob pattern or name keyword."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Name keyword or glob pattern"},
            "path": {"type": "string", "description": "Directory to search"},
            "limit": {"type": "integer", "description": "Max matched files"},
        },
        "required": ["query"],
    }

    def execute(self, query: str, path: str = "", limit: int = 100) -> str:
        try:
            path = path or str(SANDBOX_ROOT)
            base = Path(path).expanduser().resolve()
            denied = ensure_within_sandbox(base)
            if denied:
                return f"Error: {denied}"
            if not base.exists() or not base.is_dir():
                return f"Error: {path} is not a directory"

            limit = max(1, min(limit, 1000))
            q = query.strip()
            glob_mode = any(ch in q for ch in "*?[]")

            results: list[Path] = []
            if glob_mode:
                results = [p for p in base.rglob(q) if p.is_file()]
            else:
                low = q.lower()
                for p in base.rglob("*"):
                    if p.is_file() and low in p.name.lower():
                        results.append(p)
                        if len(results) >= limit:
                            break

            if glob_mode:
                results = results[:limit]

            if not results:
                return "No files matched."
            return "\n".join(str(p) for p in results)
        except Exception as e:
            return f"Error: {e}"
