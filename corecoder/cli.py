"""Interactive REPL - the user-facing terminal interface."""

import sys
import os
import argparse
import re

# Fix Windows encoding issues
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.live import Live
from .agent import Agent
from .llm import LLM
from .config import Config
from .multi_agent import SuperAssistant, HAS_LANGGRAPH
from .session import save_session, load_session, list_sessions
from .skills import load_skills
from .tools.sandbox import SANDBOX_ROOT
from . import __version__

console = Console(force_terminal=True)
_FIXED_HEADINGS = [
    "已实现功能",
    "项目结构摘要",
    "实际执行命令",
    "验证结果",
    "已知限制",
    "下一步建议",
    "协作编排执行记录",
]
_TASK_INTENT_KEYWORDS = (
    "总结",
    "总结一下",
    "报告",
    "交付",
    "实现",
    "开发",
    "构建",
    "重构",
    "修复",
    "测试",
    "审查",
    "review",
    "summary",
    "deliver",
    "implement",
    "build",
    "refactor",
    "fix",
    "test",
    "audit",
)


def _api_key_setup_examples() -> str:
    """Return platform-aware API key setup examples for Windows/Linux."""
    is_windows = sys.platform == "win32"
    shell_name = "PowerShell (Windows)" if is_windows else "Bash/Zsh (Linux/macOS)"

    if is_windows:
        openai_cmd = '$env:OPENAI_API_KEY="sk-..."'
        deepseek_cmd = (
            '$env:OPENAI_API_KEY="sk-..."; '
            '$env:OPENAI_BASE_URL="https://api.deepseek.com"'
        )
        ollama_cmd = (
            '$env:OPENAI_API_KEY="ollama"; '
            '$env:OPENAI_BASE_URL="http://localhost:11434/v1"; '
            '$env:CORECODER_MODEL="qwen2.5-coder"'
        )
    else:
        openai_cmd = "export OPENAI_API_KEY=sk-..."
        deepseek_cmd = "export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com"
        ollama_cmd = (
            "export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 "
            "CORECODER_MODEL=qwen2.5-coder"
        )

    windows_note = (
        "\nTip: For persistent vars on Windows, use:\n"
        '  setx OPENAI_API_KEY "sk-..."\n'
        "Then open a new terminal."
    )
    linux_note = "\nTip: Add exports to ~/.bashrc or ~/.zshrc for persistence."

    return (
        "Set one of: OPENAI_API_KEY, DEEPSEEK_API_KEY, or CORECODER_API_KEY\n"
        f"\nDetected shell style: {shell_name}\n"
        "\nExamples:\n"
        "  # OpenAI\n"
        f"  {openai_cmd}\n"
        "\n"
        "  # DeepSeek\n"
        f"  {deepseek_cmd}\n"
        "\n"
        "  # Ollama (local)\n"
        f"  {ollama_cmd}\n"
        + (windows_note if is_windows else linux_note)
    )


def _parse_args():
    p = argparse.ArgumentParser(
        prog="corecoder",
        description="Minimal AI coding agent. Works with any OpenAI-compatible LLM.",
    )
    p.add_argument("-m", "--model", help="Model name (default: $CORECODER_MODEL or gpt-4o)")
    p.add_argument("--base-url", help="API base URL (default: $OPENAI_BASE_URL)")
    p.add_argument("--api-key", help="API key (default: $OPENAI_API_KEY)")
    p.add_argument("-p", "--prompt", help="One-shot prompt (non-interactive mode)")
    p.add_argument("--super-assistant", action="store_true", help="Enable supervisor + experts mode")
    p.add_argument(
        "--session-persist",
        action="store_true",
        help="Persist SuperAssistant session under sandbox (env CORECODER_SESSION_PERSIST)",
    )
    p.add_argument("-r", "--resume", metavar="ID", help="Resume a saved session")
    p.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args()


def main():
    args = _parse_args()
    config = Config.from_env()
     
    # CLI args override env vars 命令行优于环境变量设置
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.api_key:
        config.api_key = args.api_key
    if args.super_assistant:
        config.super_assistant = True
    if args.session_persist:
        config.session_persist = True

     #如果没有找到API密钥，提示用户设置环境变量并退出
    if not config.api_key:
        console.print("[red bold]No API key found.[/]")
        console.print(_api_key_setup_examples())
        sys.exit(1)
     #程序异常退出时，显示错误信息 
    llm = LLM(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )
    skills = load_skills()
    agent = Agent(llm=llm, skills=skills, max_context_tokens=config.max_context_tokens)
    _ssp = (config.session_snapshot_path or "").strip()
    super_assistant = SuperAssistant(
        llm=llm,
        persist_session=config.session_persist,
        session_snapshot_path=_ssp if _ssp else None,
    )

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
#这个过程进行加载数据的，如果找到和找不到的时候异常退出，并且显示错误信息
    # one-shot mode，单词执行
    if args.prompt:
        _run_once(agent, args.prompt, super_assistant if config.super_assistant else None)
        return

    # interactive REPL，交互式命令行界面
    _repl(agent, config, super_assistant)


def _run_once(agent: Agent, prompt: str, super_assistant: SuperAssistant | None = None):
    """Non-interactive: run one prompt and exit."""
    def on_token(tok):
        # Use plain print with UTF-8 encoding to avoid Windows issues
        try:
            print(tok, end="", flush=True)
        except UnicodeEncodeError:
            # Fallback: encode problematic characters
            safe_tok = tok.encode('gbk', errors='replace').decode('gbk')
            print(safe_tok, end="", flush=True)

    def on_tool(name, kwargs):
        print(f"\n$ {name}({_brief(kwargs)})")
#这个函数表示在干什么，调用什么工具，并且把结果打印出来
    if super_assistant:
        print(super_assistant.run(prompt))
    else:
        # Use XML-based tool calling for local models
        agent.chat_with_xml_tools(prompt, on_token=on_token, on_tool=on_tool)
        print()

#交互式面板,就是运行之后的那个上面的显示，包括模型名称，版本号，提示信息等
def _repl(agent: Agent, config: Config, super_assistant: SuperAssistant):
    """Interactive read-eval-print loop."""
    console.print(Panel(
        f"[bold cyan]$ CoreCoder[/bold cyan] v{__version__}\n"
        f"[bold cyan]$ Model:[/bold cyan] [cyan]{config.model}[/cyan]"
        + (f"  [bold cyan]$ Base:[/bold cyan] [cyan]{config.base_url}[/cyan]" if config.base_url else "")
        + f"\n[bold cyan]$ SuperAssistant:[/bold cyan] [cyan]{'on' if config.super_assistant else 'off'}[/cyan]"
        + f"  [bold cyan]$ LangGraph:[/bold cyan] [cyan]{'enabled' if HAS_LANGGRAPH else 'fallback'}[/cyan]"
        + "\n[bold cyan]$ Type /help for commands, Ctrl+C to cancel, quit to exit.[/bold cyan]"
        + "\n[bold cyan]$ SuperAssistant session:[/bold cyan] [cyan]/context[/cyan] 查看，"
        + "[cyan]/context clear[/cyan] 清空，[cyan]/context persist on|off|reload[/cyan] 持久化",
        border_style="cyan",
    ))
    if config.super_assistant:
        console.print(
            Panel(
                super_assistant.orchestration_debug_preview(),
                title="SuperAssistant Orchestration Debug",
                border_style="cyan",
            )
        )
    #保存命令历史
    hist_path = os.path.expanduser("~/.corecoder_history")
    history = FileHistory(hist_path)

    # Enter submits, Escape+Enter inserts a newline (for pasting code blocks etc.)
    kb = KeyBindings()
#绑定键盘，创建快捷键，Enter键提交输入，Escape+Enter插入换行符，适合粘贴代码块等多行输入的场景
    @kb.add("enter")
    def _submit(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")
#控制是否验证输出，是否流式显示输出
    validate_output = True
    stream_output = False
#提示符，历史记录，键盘快捷操作绑定，多行输入，续行提示符
    while True:
        try:
            user_input = pt_prompt(
                "You > ",
                history=history,
                multiline=True,
                key_bindings=kb,
                prompt_continuation="...  ",
            ).strip() #去除收尾空白
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
                border_style="cyan",#边框是青色
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
            if config.super_assistant:
                super_assistant.clear_session_context()
            console.print("[yellow]Conversation reset（含 SuperAssistant 会话上下文）.[/yellow]")
            continue
        if user_input == "/tokens":
            p = agent.llm.total_prompt_tokens#模型输入消耗，输出消耗
            c = agent.llm.total_completion_tokens
            line = f"Tokens: [cyan]{p}[/cyan] prompt + [cyan]{c}[/cyan] completion = [bold]{p+c}[/bold] total"
            cost = agent.llm.estimated_cost#费用的估算
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
        if user_input == "/compact":#压缩对话历史信息
            from .context import estimate_tokens
            before = estimate_tokens(agent.messages)
            compressed = agent.context.maybe_compress(agent.messages, agent.llm)
            after = estimate_tokens(agent.messages)
            if compressed:
                console.print(f"[green]Compressed: {before} -> {after} tokens ({len(agent.messages)} messages)[/green]")
            else:
                console.print(f"[dim]Nothing to compress ({before} tokens, {len(agent.messages)} messages)[/dim]")
            continue
        if user_input == "/save":#保存对话信息
            sid = save_session(agent.messages, config.model)
            console.print(f"[green]Session saved: {sid}[/green]")
            console.print(f"Resume with: corecoder -r {sid}")
            continue
        if user_input == "/diff":#显示当前会话中修改过的文件列表，只会显示通过工具函数修改过的文件，手动修改的文件不会被记录和显示
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
        if user_input == "/skills":
            if not agent.skills:
                console.print("[dim]No skills loaded. Add .cursor/skills/<name>/SKILL.md or ~/.cursor/skills/<name>/SKILL.md[/dim]")
            else:
                console.print(f"[bold]Loaded skills ({len(agent.skills)}):[/bold]")
                for s in agent.skills:
                    desc = f" - {s.description}" if s.description else ""
                    console.print(f"  [cyan]{s.name}[/cyan]{desc}")
            continue
        if user_input == "/validate-output" or user_input.startswith("/validate-output "):
            arg = user_input[len("/validate-output"):].strip().lower()
            if not arg:
                state = "on" if validate_output else "off"
                console.print(f"Output validation is [cyan]{state}[/cyan]")
                continue
            if arg in ("on", "off"):
                validate_output = (arg == "on")
                console.print(f"Output validation switched [cyan]{arg}[/cyan]")
            else:
                console.print("[yellow]Usage: /validate-output on|off[/yellow]")
            continue
        if user_input == "/stream" or user_input.startswith("/stream "):
            arg = user_input[len("/stream"):].strip().lower()
            if not arg:
                state = "on" if stream_output else "off"
                console.print(f"Streaming output is [cyan]{state}[/cyan]")
                continue
            if arg in ("on", "off"):
                stream_output = (arg == "on")
                console.print(f"Streaming output switched [cyan]{arg}[/cyan]")
            else:
                console.print("[yellow]Usage: /stream on|off[/yellow]")
            continue
        if user_input == "/context" or user_input.startswith("/context "):
            if not config.super_assistant:
                console.print("[yellow]Super assistant mode is off. Use /assistant on first.[/yellow]")
                continue
            arg = user_input[len("/context"):].strip()
            arg_lower = arg.lower()
            if arg_lower.startswith("persist "):
                sub = arg_lower[len("persist ") :].strip()
                if sub == "on":
                    config.session_persist = True
                    st = super_assistant.set_session_persist(True, reload_from_disk=False)
                    if st.get("write_error"):
                        console.print(f"[red]持久化已开启，但写入快照失败：{st['write_error']}[/red]")
                    else:
                        console.print(
                            "[green]SuperAssistant 会话持久化已开启。[/green]\n"
                            f"[dim]快照路径：{st['snapshot_path']}（每轮专家执行结束后写入；"
                            "此前 persist=off 时不会产生该文件）[/dim]"
                        )
                elif sub == "off":
                    config.session_persist = False
                    super_assistant.set_session_persist(False)
                    console.print("[yellow]SuperAssistant 会话持久化已关闭（已有 .session.json 仍保留）。[/yellow]")
                elif sub == "reload":
                    config.session_persist = True
                    st = super_assistant.set_session_persist(True, reload_from_disk=True)
                    if st.get("write_error"):
                        console.print(f"[red]写入快照失败：{st['write_error']}[/red]")
                    elif st.get("loaded_from_disk"):
                        console.print(
                            f"[green]已从磁盘加载会话快照（{st.get('turns_loaded', 0)} 条轮次摘要）。[/green]\n"
                            f"[dim]{st['snapshot_path']}[/dim]"
                        )
                    else:
                        console.print(
                            "[yellow]未找到有效快照（例如从未开启 persist 或快照已删）。[/yellow]\n"
                            f"[dim]{st.get('note', '')}[/dim]\n"
                            f"[dim]已开启持久化，并将当前内存会话写入：{st['snapshot_path']}[/dim]"
                        )
                else:
                    console.print("[yellow]Usage: /context persist on|off|reload[/yellow]")
                continue
            if arg_lower == "clear":
                super_assistant.clear_session_context()
                console.print("[green]Supervisor 会话上下文已清空（若开启持久化则已删除快照文件）。[/green]")
                continue
            console.print(
                Panel(
                    super_assistant.session_context_preview(),
                    title="SuperAssistant Session Context",
                    border_style="cyan",
                )
            )
            continue
        if user_input == "/assistant" or user_input.startswith("/assistant "):
            arg = user_input[len("/assistant"):].strip().lower()
            if not arg:
                state = "on" if config.super_assistant else "off"
                console.print(f"Super assistant mode is [cyan]{state}[/cyan]")
                continue
            if arg in ("on", "off"):
                config.super_assistant = (arg == "on")
                console.print(f"Super assistant mode switched [cyan]{arg}[/cyan]")
            else:
                console.print("[yellow]Usage: /assistant on|off[/yellow]")
            continue
        if user_input == "/reload-mappings":
            if not config.super_assistant:
                console.print("[yellow]Super assistant mode is off. Use /assistant on first.[/yellow]")
                continue
            stats = super_assistant.reload_mappings()
            console.print(
                "[green]Browser site mappings reloaded:[/green] "
                f"query_aliases={stats['query_aliases']}, "
                f"canonical_query={stats['canonical_query']}, "
                f"strict_domains={stats['strict_domains']}"
            )
            continue
        if user_input == "/show-mappings":
            if not config.super_assistant:
                console.print("[yellow]Super assistant mode is off. Use /assistant on first.[/yellow]")
                continue
            mappings = super_assistant.show_mappings()
            parts: list[str] = []
            for section in ("query_aliases", "canonical_query", "strict_domains"):
                section_map = mappings.get(section, {})
                parts.append(f"[bold cyan]{section} ({len(section_map)}):[/bold cyan]")
                for k in sorted(section_map):
                    parts.append(f"  {k} -> {section_map[k]}")
            console.print(Panel("\n".join(parts), title="Browser Site Mappings", border_style="cyan"))
            continue
        if user_input == "/show-file-tools":
            if not config.super_assistant:
                console.print("[yellow]Super assistant mode is off. Use /assistant on first.[/yellow]")
                continue
            tools = super_assistant.show_file_tools()
            if not tools:
                console.print("[yellow]No tools are currently available for file expert.[/yellow]")
            else:
                lines = ["[bold cyan]File expert available tools:[/bold cyan]"]
                lines.extend(f"  - {name}" for name in tools)
                console.print(Panel("\n".join(lines), title="File Expert Tools", border_style="cyan"))
            continue
        if user_input == "/sandbox":
            console.print(
                Panel(
                    f"[bold cyan]Current sandbox root:[/bold cyan]\n{SANDBOX_ROOT}",
                    title="CoreCoder Sandbox",
                    border_style="cyan",
                )
            )
            continue
        if user_input == "/debug":
            if not config.super_assistant:
                console.print("[yellow]Super assistant mode is off. Use /assistant on first.[/yellow]")
                continue
            console.print(
                Panel(
                    super_assistant.orchestration_debug_preview(),
                    title="SuperAssistant Orchestration Debug",
                    border_style="cyan",
                )
            )
            continue
        if user_input == "/files-help":
            sandbox_root = str(SANDBOX_ROOT)
            console.print(Panel(
                f"[bold cyan]$ File Tools (sandbox: {sandbox_root}):[/bold cyan]\n"
                "\n"
                "[bold cyan]$ 1) list_directory[/bold cyan]\n"
                f"  Params: path='{sandbox_root}', recursive=false, limit=200\n"
                f"  Example: 列出 {sandbox_root} 下前 50 个文件\n"
                "\n"
                "[bold cyan]$ 2) read_file[/bold cyan]\n"
                "  Params: file_path, offset=1, limit=2000\n"
                f"  Example: 读取 {sandbox_root}\\notes.md 前 100 行\n"
                "\n"
                "[bold cyan]$ 3) write_file[/bold cyan]\n"
                "  Params: file_path, content\n"
                f"  Example: 将内容写入 {sandbox_root}\\todo.txt\n"
                "\n"
                "[bold cyan]$ 4) append_file[/bold cyan]\n"
                "  Params: file_path, content\n"
                f"  Example: 在 {sandbox_root}\\todo.txt 末尾追加一行\n"
                "\n"
                "[bold cyan]$ 5) edit_file[/bold cyan]\n"
                "  Params: file_path, old_string, new_string\n"
                "  Example: 把 config.json 里的 old_value 替换成 new_value\n"
                "\n"
                "[bold cyan]$ 6) search_files[/bold cyan]\n"
                f"  Params: query, path='{sandbox_root}', limit=100\n"
                f"  Example: 在 {sandbox_root} 搜索文件名包含 report 的文件\n"
                "\n"
                "[bold cyan]$ 7) grep_in_files[/bold cyan]\n"
                f"  Params: query, path='{sandbox_root}', include(optional)\n"
                f"  Example: 在 {sandbox_root} 的 *.md 中搜索 TODO\n"
                "\n"
                "[bold cyan]$ 8) file_info[/bold cyan]\n"
                "  Params: path\n"
                f"  Example: 查看 {sandbox_root}\\notes.md 的大小和修改时间\n",
                title="CoreCoder File Tools Help",
                border_style="cyan",
            ))
            continue

        # call the agent
        streamed_tokens: list[str] = []

        def on_token(tok):
            streamed_tokens.append(tok)
            if stream_output:
                try:
                    print(tok, end="", flush=True)#实时打印，不换行
                except UnicodeEncodeError:
                    safe_tok = tok.encode("gbk", errors="replace").decode("gbk")
                    print(safe_tok, end="", flush=True)

        def on_tool(name, kwargs):
            console.print(f"\n[dim]$ {name}({_brief(kwargs)})[/dim]")

        try:
            if config.super_assistant:
                response = super_assistant.run(user_input)
                raw_response = response
            else:
                # Use XML-based tool calling for local models
                response = agent.chat_with_xml_tools(user_input, on_token=on_token, on_tool=on_tool)
                raw_response = "".join(streamed_tokens).strip() if streamed_tokens else response
            if stream_output and raw_response:#一个空行，更美观
                console.print()

            if validate_output:
                cleaned = _sanitize_final_output(raw_response)
                should_enforce_headings = _should_enforce_structured_output(
                    user_input, super_assistant_mode=config.super_assistant
                )
                if should_enforce_headings and not _has_required_headings(cleaned):
                    rewrite_prompt = _build_rewrite_prompt(cleaned)
                    rewrite = agent.chat_with_xml_tools(rewrite_prompt, on_token=None, on_tool=on_tool)
                    cleaned = _sanitize_final_output(rewrite)
                    if not _has_required_headings(cleaned):
                        missing = _missing_headings(cleaned)
                        cleaned = (
                            "[输出校验未通过]\n"
                            f"缺失固定标题: {', '.join(missing)}\n\n"
                            + cleaned
                        )
                panel_title = "Sanitized Response" if stream_output else "Response"
                console.print(Panel(cleaned, title=panel_title, border_style="green"))
            else:
                if not stream_output:
                    console.print(Panel(raw_response, title="Response (raw)", border_style="yellow"))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
        except Exception as e:
            console.print(f"\n[red]Error: {_friendly_error_message(e)}[/red]")

#快捷帮助，帮助快速理解的
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
        "  /skills        Show loaded skills\n"
        "  /validate-output on|off  Toggle output sanitizer/validator\n"
        "  /stream on|off  Toggle token-by-token streaming display\n"
        "  /assistant on|off  Toggle supervisor + experts mode\n"
        "  /context       Show SuperAssistant session context (last file, route, turns)\n"
        "  /context clear Clear session (and delete snapshot if persist is on)\n"
        "  /context persist on|off|reload  Toggle disk persistence or reload from .session.json\n"
        "  --session-persist  CLI flag: enable persist for this process\n"
        "  Env: CORECODER_SANDBOX_ROOT, CORECODER_SESSION_PERSIST, CORECODER_SESSION_PATH (under sandbox only)\n"
        "  Note: .session.json is written only while persist is on; off = in-memory only until exit.\n"
        "  /reload-mappings  Reload browser site mapping config\n"
        "  /show-mappings  Show active browser site mappings\n"
        "  /show-file-tools  Show current tools available to file expert\n"
        "  /sandbox       Show current effective sandbox root\n"
        "  /debug         Show registered planners/actions and risk policy\n"
        "  /files-help  Show file tools params and examples\n"
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


def _friendly_error_message(exc: Exception) -> str:
    """Convert low-level exceptions to actionable Chinese hints."""
    raw = str(exc) or exc.__class__.__name__
    lower = raw.lower()

    is_encoding_issue = isinstance(exc, UnicodeDecodeError) or (
        "codec can't decode" in lower
        or ("unicode" in lower and "decode" in lower)
        or "_readerthread" in lower
        or "gbk" in lower
    )
    if is_encoding_issue:
        return (
            "检测到编码解码错误（常见于 Windows 的 GBK/UTF-8 混用）。\n"
            "建议操作：\n"
            "1) 在 PowerShell 执行 `chcp 65001` 后重试；\n"
            "2) 尽量使用 UTF-8 输出的命令或工具；\n"
            "3) 若仍失败，重启终端后再运行 CoreCoder。"
        )

    return raw

#移除协议标签和去重段落
def _sanitize_final_output(text: str) -> str:
    """Sanitize model output by stripping protocol tags and deduplicating paragraphs."""
    lines = text.splitlines()#内置方法，不保留换行符，false表示不保留换行符，true表示保留换行符
    filtered: list[str] = []
    tag_pat = re.compile(r"</?(tool_call|function|parameter)(?:[=>].*)?>")
    for line in lines:
        if tag_pat.search(line.strip()):
            continue
        if line.strip().startswith("<function=") or line.strip().startswith("<parameter="):
            continue
        filtered.append(line)

    # Deduplicate repeated paragraphs while preserving order.
    blocks = [b.strip() for b in "\n".join(filtered).split("\n\n")]
    seen: set[str] = set()
    unique_blocks: list[str] = []
    for block in blocks:
        if not block:#若是空白的
            continue
        key = re.sub(r"\s+", " ", block).strip()#正则表达式，\s匹配任何空白字符，+匹配一个或者多个
        if key in seen:#如果出现了就添加，用于检测后续的重复
            continue
        seen.add(key)
        unique_blocks.append(block)
    return "\n\n".join(unique_blocks).strip()

#标题规范化，去除多余的空白和符号，统一格式，便于后续的校验和处理
def _normalize_heading(line: str) -> str:
    return line.strip().lstrip("#").lstrip("-").strip()


def _missing_headings(text: str) -> list[str]:
    present = {_normalize_heading(line) for line in text.splitlines() if line.strip()}#函数式编程的思想
    return [h for h in _FIXED_HEADINGS if h not in present]
 
 #判断文本是否包含所有必需的标题。
def _has_required_headings(text: str) -> bool:
    return not _missing_headings(text)

#生成一个格式指令，告诉 AI 如何修正它之前不规范的输出
def _build_rewrite_prompt(previous: str) -> str:
    headings = "\n".join(f"- {h}" for h in _FIXED_HEADINGS)
    return (
        "请重写你上一条回复，并严格通过输出校验：\n"
        "1) 不要包含任何 tool_call/function/parameter 协议标签；\n"
        "2) 删除重复段落；\n"
        "3) 仅使用以下7个固定标题且全部包含：\n"
        f"{headings}\n"
        "4) 标题外不要追加其它章节。\n\n"
        "这是待重写内容：\n"
        f"{previous}"
    )

#是否需要结构化输出
def _should_enforce_structured_output(user_input: str, super_assistant_mode: bool = False) -> bool:
    """Enable 7-section enforcement only for task/report-like prompts."""
    if super_assistant_mode:
        # SuperAssistant returns role/evidence structured outputs and may include
        # operation confirmations that should not be forced into 7 fixed sections.
        return False
    text = user_input.strip().lower()
    if not text:
        return False
    if text.startswith("/"):
        return False
    return any(k in text for k in _TASK_INTENT_KEYWORDS)
