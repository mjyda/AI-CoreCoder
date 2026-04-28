"""Configuration - env vars and defaults."""

import os
from dataclasses import dataclass
from pathlib import Path
#个配置管理模块，用于从环境变量和 .env 文件中加载 AI 模型的配置参数

def _load_dotenv():
    """Load .env from cwd, walking up to home dir. No-op if python-dotenv missing."""
    try:
        from dotenv import load_dotenv
        # search cwd first, then parent dirs up to ~
        env_path = Path(".env")
        if not env_path.exists():
            cur = Path.cwd()
            home = Path.home()
            while cur != home and cur != cur.parent:#当前目录不是用户主目录或者根目录的时候，防止无限循环
                candidate = cur / ".env"
                if candidate.exists():
                    env_path = candidate
                    break
                cur = cur.parent
        load_dotenv(env_path, override=True)#覆盖已有环境变量，确保以项目 .env 配置为准
    except ImportError:
        pass  # python-dotenv not installed, silently skip，pass表示什么都不做，继续执行后面的代码

#自动生成类的特殊方法，减少编写简单数据类时的样板代码。
@dataclass
class Config:
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    max_context_tokens: int = 128_000
    super_assistant: bool = False
    # Persist SuperAssistant session context to disk (cross-restart “memory”).
    session_persist: bool = False
    session_snapshot_path: str = ""

    @classmethod#绑定到类而不是实例的方法
    def from_env(cls) -> "Config":
        # load .env if present (won't override existing env vars)
        _load_dotenv()
        # pick up common env vars automatically
        api_key = (
            os.getenv("CORECODER_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or ""
        )
        return cls(
            model=os.getenv("CORECODER_MODEL", "gpt-4o"),
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL") or os.getenv("CORECODER_BASE_URL"),
            max_tokens=int(os.getenv("CORECODER_MAX_TOKENS", "4096")),
            temperature=float(os.getenv("CORECODER_TEMPERATURE", "0")),
            max_context_tokens=int(os.getenv("CORECODER_MAX_CONTEXT", "128000")),
            super_assistant=os.getenv("CORECODER_SUPER_ASSISTANT", "").lower() in ("1", "true", "yes", "on"),
            session_persist=os.getenv("CORECODER_SESSION_PERSIST", "").lower() in ("1", "true", "yes", "on"),
            session_snapshot_path=os.getenv("CORECODER_SESSION_PATH", "").strip(),
        )
