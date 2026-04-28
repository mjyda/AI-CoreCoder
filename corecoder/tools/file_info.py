"""File metadata inspection."""

from datetime import datetime
from pathlib import Path

from .base import Tool
from .sandbox import ensure_within_sandbox


class FileInfoTool(Tool):
    name = "file_info"
    description = "Get file or directory metadata."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Target file/directory path"},
        },
        "required": ["path"],
    }

    def execute(self, path: str) -> str:
        try:
            p = Path(path).expanduser().resolve()
            denied = ensure_within_sandbox(p)
            if denied:
                return f"Error: {denied}"
            if not p.exists():
                return f"Error: {path} not found"

            st = p.stat()
            kind = "directory" if p.is_dir() else "file"
            mtime = datetime.fromtimestamp(st.st_mtime).isoformat(sep=" ", timespec="seconds")
            ctime = datetime.fromtimestamp(st.st_ctime).isoformat(sep=" ", timespec="seconds")
            return (
                f"path: {p}\n"
                f"type: {kind}\n"
                f"size_bytes: {st.st_size}\n"
                f"modified_at: {mtime}\n"
                f"created_at: {ctime}"
            )
        except Exception as e:
            return f"Error: {e}"
