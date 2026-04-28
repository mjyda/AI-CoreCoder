"""Tool registry."""

from .bash import BashTool
from .list_directory import ListDirectoryTool
from .read import ReadFileTool
from .write import WriteFileTool
from .append import AppendFileTool
from .edit import EditFileTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .search_files import SearchFilesTool
from .grep_in_files import GrepInFilesTool
from .file_info import FileInfoTool
from .agent import AgentTool
from .browser_history import BrowserHistoryTool
from .mail_imap import (
    GmailDeleteEmailTool,
    GmailGetContentTool,
    GmailListRecentTool,
    GmailMarkReadTool,
    GmailMoveToTrashTool,
    GmailReplyEmailTool,
    GmailSearchTool,
    GmailSendEmailTool,
)

ALL_TOOLS = [
    BashTool(),
    ListDirectoryTool(),
    ReadFileTool(),
    WriteFileTool(),
    AppendFileTool(),
    EditFileTool(),
    SearchFilesTool(),
    GrepInFilesTool(),
    FileInfoTool(),
    GlobTool(),
    GrepTool(),
    BrowserHistoryTool(),
    GmailListRecentTool(),
    GmailSearchTool(),
    GmailGetContentTool(),
    GmailSendEmailTool(),
    GmailReplyEmailTool(),
    GmailDeleteEmailTool(),
    GmailMarkReadTool(),
    GmailMoveToTrashTool(),
    AgentTool(),
]


def get_tool(name: str):
    """Look up a tool by name."""
    for t in ALL_TOOLS:
        if t.name == name:
            return t
    return None
