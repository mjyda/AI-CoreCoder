"""Interactive REPL - the user-facing terminal interface."""

import sys
import os
import argparse

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

from .agent import Agent
from .llm import LLM
from .config import Config
from .session import save_session, load_session, list_sessions
from . import __version__

console = Console()


def _parse_args():
    p = argparse.ArgumentParser(
        prog="corecoder",
        description="Minimal AI coding agent. Works with any OpenAI-compatible LLM.",
    )
    p.add_argument("-m", "--model", help="Model name (default: $CORECODER_MODEL or gpt-4o)")
    p.add_argument("--base-url", help="API base URL (default: $OPENAI_BASE_URL)")
    p.add_argument("--api-key", help="API key (default: $OPENAI_API_KEY)")
    p.add_argument("-p", "--prompt", help="One-shot prompt (non-interactive mode)")
    p.add_argument("-r", "--resume", metavar="ID", help="Resume a saved session")
    p.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args()


def main():
    args = _parse_args()
    config = Config.from_env()

    # CLI args override env vars
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.api_key:
        config.api_key = args.api_key

    if not config.api_key:
        console.print("[red bold]No API key found.[/]")
        console.print(
            "Set one of: OPENAI_API_KEY, DEEPSEEK_API_KEY, or CORECODER_API_KEY\n"
            "\nExamples:\n"
            "  # OpenAI\n"
            "  export OPENAI_API_KEY=sk-...\n"
            "\n"
            "  # DeepSeek\n"
            "  export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com\n"
            "\n"
            "  # Ollama (local)\n"
            "  export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 CORECODER_MODEL=qwen2.5-coder\n"
        )
        sys.exit(1)

    llm = LLM(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )
    agent = Agent(llm=llm, max_context_tokens=config.max_context_tokens)

    # resume saved session
    if args.resume:
        loaded = load_session(args.resume)
        if loaded:
            agent.messages, loaded_model = loaded
            # restore the model from the saved session unless overridden by CLI
            if not args.model:
                agent.llm.model = loaded_model
                config.model = loaded_model
            console.print(f"[green]Resumed session: {args.resume} (model: {agent.llm.model})[/green]")
        else:
            console.print(f"[red]Session '{args.resume}' not found.[/red]")
            sys.exit(1)

    # one-shot mode
    if args.prompt:
        _run_once(agent, args.prompt)
        return

    # interactive REPL
    _repl(agent, config)


def _run_once(agent: Agent, prompt: str):
    """Non-interactive: run one prompt and exit."""
    def on_token(tok):
        # Use Rich console for consistent formatting
        console.print(tok, end="")

    def on_tool(name, kwargs):
        console.print(f"\n[dim]$ {name}({_brief(kwargs)})[/dim]")

    agent.chat(prompt, on_token=on_token, on_tool=on_tool)
    console.print()


def _repl(agent: Agent, config: Config):
    """Interactive read-eval-print loop."""
    console.print(Panel(
        f"[bold cyan]$ CoreCoder[/bold cyan] v{__version__}\n"
        f"[bold cyan]$ Model:[/bold cyan] [cyan]{config.model}[/cyan]"
        + (f"  [bold cyan]$ Base:[/bold cyan] [cyan]{config.base_url}[/cyan]" if config.base_url else "")
        + "\n[bold cyan]$ Type /help for commands, Ctrl+C to cancel, quit to exit.[/bold cyan]",
        border_style="cyan",
    ))

    hist_path = os.path.expanduser("~/.corecoder_history")
    history = FileHistory(hist_path)

    # Enter submits, Escape+Enter inserts a newline (for pasting code blocks etc.)
    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    while True:
        try:
            user_input = pt_prompt(
                "You > ",
                history=history,
                multiline=True,
                key_bindings=kb,
                prompt_continuation="...  ",
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye!")
            break

        if not user_input:
            continue

        # Special handling for capability questions
        if user_input in ("你能做什么", "你能干什么", "what can you do", "what are your capabilities"):
            console.print(Panel(
                "[bold cyan]$ 我能做的主要工作:[/bold cyan]\n"
                "[bold cyan]$ 编程与代码编写:[/bold cyan]\n"
                "  • 根据需求编写新代码\n"
                "  • 修改和优化现有代码\n"
                "  • 代码重构和改进\n"
                "  • 解释复杂代码逻辑\n"
                "\n"
                "[bold cyan]$ 代码分析与调试:[/bold cyan]\n"
                "  • 查找和修复bug\n"
                "  • 代码审查\n"
                "  • 性能优化建议\n"
                "  • 错误排查\n"
                "\n"
                "[bold cyan]$ 项目管理:[/bold cyan]\n"
                "  • 文件和目录操作\n"
                "  • 代码搜索和定位\n"
                "  • 依赖管理和包安装\n"
                "  • 测试运行和调试\n"
                "\n"
                "[bold cyan]$ 开发工具集成:[/bold cyan]\n"
                "  • Git操作（提交、拉取、合并等）\n"
                "  • 命令行执行\n"
                "  • 多语言支持（Python, JavaScript, TypeScript, Java等）",
                border_style="cyan",
            ))
            continue

        # built-in commands
        if user_input.lower() in ("quit", "exit", "/quit", "/exit"):
            break
        if user_input == "/help":
            _show_help()
            continue
        if user_input == "/reset":
            agent.reset()
            console.print("[yellow]Conversation reset.[/yellow]")
            continue
        if user_input == "/tokens":
            p = agent.llm.total_prompt_tokens
            c = agent.llm.total_completion_tokens
            line = f"Tokens: [cyan]{p}[/cyan] prompt + [cyan]{c}[/cyan] completion = [bold]{p+c}[/bold] total"
            cost = agent.llm.estimated_cost
            if cost is not None:
                line += f"  (~${cost:.4f})"
            console.print(line)
            continue
        if user_input == "/model" or user_input.startswith("/model "):
            new_model = user_input[7:].strip() if user_input.startswith("/model ") else ""
            if new_model:
                agent.llm.model = new_model
                config.model = new_model
                console.print(f"Switched to [cyan]{new_model}[/cyan]")
            else:
                console.print(f"Current model: [cyan]{config.model}[/cyan]")
            continue
        if user_input == "/compact":
            from .context import estimate_tokens
            before = estimate_tokens(agent.messages)
            compressed = agent.context.maybe_compress(agent.messages, agent.llm)
            after = estimate_tokens(agent.messages)
            if compressed:
                console.print(f"[green]Compressed: {before} -> {after} tokens ({len(agent.messages)} messages)[/green]")
            else:
                console.print(f"[dim]Nothing to compress ({before} tokens, {len(agent.messages)} messages)[/dim]")
            continue
        if user_input == "/save":
            sid = save_session(agent.messages, config.model)
            console.print(f"[green]Session saved: {sid}[/green]")
            console.print(f"Resume with: corecoder -r {sid}")
            continue
        if user_input == "/diff":
            from .tools.edit import _changed_files
            if not _changed_files:
                console.print("[dim]No files modified this session.[/dim]")
            else:
                console.print(f"[bold]Files modified this session ({len(_changed_files)}):[/bold]")
                for f in sorted(_changed_files):
                    console.print(f"  [cyan]{f}[/cyan]")
            continue
        if user_input == "/sessions":
            sessions = list_sessions()
            if not sessions:
                console.print("[dim]No saved sessions.[/dim]")
            else:
                for s in sessions:
                    console.print(f"  [cyan]{s['id']}[/cyan] ({s['model']}, {s['saved_at']}) {s['preview']}")
            continue

        # call the agent
        streamed: list[str] = []

        # Code block state
        in_code_block = False
        code_buffer = []
        # Non-code content buffer
        text_buffer = []

        def on_token(tok):
            nonlocal in_code_block, code_buffer, text_buffer
            streamed.append(tok)

            # If token contains backtick markers, we need to handle state changes
            if "```" in tok:
                # Process token character by character to handle state changes properly
                result = ""
                i = 0
                while i < len(tok):
                    if tok[i:i+3] == "```":
                        # Found backtick marker
                        if in_code_block:
                            # Exiting code block - display buffered code in a cyan panel
                            if code_buffer:
                                code_text = "".join(code_buffer)
                                console.print(Panel(code_text, title="Code", border_style="cyan"))
                                code_buffer.clear()
                            result += "```"
                            in_code_block = False
                        else:
                            # Entering code block - display buffered text in a green panel
                            if text_buffer:
                                text_content = "".join(text_buffer)
                                if text_content.strip():
                                    console.print(Panel(text_content, title="Response", border_style="green"))
                                text_buffer.clear()
                            result += "```"
                            in_code_block = True
                        i += 3
                    else:
                        result += tok[i]
                        i += 1
                console.print(result, end="")
            else:
                # No backticks in this token
                if in_code_block:
                    # Buffer code content to apply cyan color
                    code_buffer.append(tok)
                else:
                    # Buffer text content for panel display
                    text_buffer.append(tok)

        def on_tool(name, kwargs):
            console.print(f"\n[dim]$ {name}({_brief(kwargs)})[/dim]")

        try:
            response = agent.chat(user_input, on_token=on_token, on_tool=on_tool)
            if streamed:
                # Ensure we reset code block color if still in a block
                if in_code_block:
                    if code_buffer:
                        code_text = "".join(code_buffer)
                        console.print(Panel(code_text, title="Code", border_style="cyan"))
                        code_buffer.clear()
                    in_code_block = False
                # Display any remaining text buffer
                if text_buffer:
                    text_content = "".join(text_buffer)
                    if text_content.strip():
                        console.print(Panel(text_content, title="Response", border_style="green"))
                    text_buffer.clear()
                console.print()  # newline after streamed tokens
            else:
                # response wasn't streamed (came after tool calls)
                console.print(Panel(response, title="Response", border_style="green"))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")


def _show_help():
    console.print(Panel(
        "[bold cyan]$ Commands:[/bold cyan]\n"
        "  /help          Show this help\n"
        "  /reset         Clear conversation history\n"
        "  /model         Show current model\n"
        "  /model <name>  Switch model mid-conversation\n"
        "  /tokens        Show token usage\n"
        "  /compact       Compress conversation context\n"
        "  /diff          Show files modified this session\n"
        "  /save          Save session to disk\n"
        "  /sessions      List saved sessions\n"
        "  quit           Exit CoreCoder\n"
        "\n"
        "[bold cyan]$ Input:[/bold cyan]\n"
        "  Enter          Submit message\n"
        "  Esc+Enter      Insert newline (for pasting code blocks)",
        title="CoreCoder Help",
        border_style="cyan",
    ))


def _brief(kwargs: dict, maxlen: int = 80) -> str:
    s = ", ".join(f"{k}={repr(v)[:40]}" for k, v in kwargs.items())
    return s[:maxlen] + ("..." if len(s) > maxlen else "")
