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
from .session import save_session, load_session, list_sessions
from .skills import load_skills
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
     
    # CLI args override env vars 命令行优于环境变量设置
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.api_key:
        config.api_key = args.api_key

     #如果没有找到API密钥，提示用户设置环境变量并退出
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
        _run_once(agent, args.prompt)
        return

    # interactive REPL，交互式命令行界面
    _repl(agent, config)


def _run_once(agent: Agent, prompt: str):
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
    # Use XML-based tool calling for local models
    agent.chat_with_xml_tools(prompt, on_token=on_token, on_tool=on_tool)
    print()

#交互式面板,就是运行之后的那个上面的显示，包括模型名称，版本号，提示信息等
def _repl(agent: Agent, config: Config):
    """Interactive read-eval-print loop."""
    console.print(Panel(
        f"[bold cyan]$ CoreCoder[/bold cyan] v{__version__}\n"
        f"[bold cyan]$ Model:[/bold cyan] [cyan]{config.model}[/cyan]"
        + (f"  [bold cyan]$ Base:[/bold cyan] [cyan]{config.base_url}[/cyan]" if config.base_url else "")
        + "\n[bold cyan]$ Type /help for commands, Ctrl+C to cancel, quit to exit.[/bold cyan]",
        border_style="cyan",
    ))
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
            console.print("[yellow]Conversation reset.[/yellow]")
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
            # Use XML-based tool calling for local models
            response = agent.chat_with_xml_tools(user_input, on_token=on_token, on_tool=on_tool)
            raw_response = "".join(streamed_tokens).strip() if streamed_tokens else response
            if stream_output and raw_response:#一个空行，更美观
                console.print()

            if validate_output:
                cleaned = _sanitize_final_output(raw_response)
                should_enforce_headings = _should_enforce_structured_output(user_input)
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
            console.print(f"\n[red]Error: {e}[/red]")

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
def _should_enforce_structured_output(user_input: str) -> bool:
    """Enable 7-section enforcement only for task/report-like prompts."""
    text = user_input.strip().lower()
    if not text:
        return False
    if text.startswith("/"):
        return False
    return any(k in text for k in _TASK_INTENT_KEYWORDS)
