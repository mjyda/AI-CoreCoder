"""CoreCoder - Minimal AI coding agent inspired by Claude Code's architecture."""
#初始化文件（__init__.py），用于定义包的公开接口和元数据。
__version__ = "0.2.0"

from corecoder.agent import Agent
from corecoder.llm import LLM
from corecoder.config import Config
from corecoder.tools import ALL_TOOLS

__all__ = ["Agent", "LLM", "Config", "ALL_TOOLS", "__version__"]
