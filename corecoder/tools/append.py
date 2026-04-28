"""Append content to an existing/new file."""

from pathlib import Path

from .base import Tool
from .edit import _changed_files
from .sandbox import ensure_within_sandbox


class AppendFileTool(Tool):
    name = "append_file"
    description = "Append content to a file. Creates file if missing."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path of file to append"},
            "content": {"type": "string", "description": "Content to append"},
        },
        "required": ["file_path", "content"],
    }

    def execute(self, file_path: str, content: str) -> str:
        try:
            p = Path(file_path).expanduser().resolve()
            denied = ensure_within_sandbox(p)
            if denied:
                return f"Error: {denied}"
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(content)
            _changed_files.add(str(p))
            added = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            return f"Appended {added} lines to {file_path}"
        except Exception as e:
            return f"Error: {e}"
