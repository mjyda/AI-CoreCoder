"""Core agent loop.

This is the heart of CoreCoder.  The pattern is simple:

    user message -> LLM (with tools) -> tool calls? -> execute -> loop
                                      -> text reply? -> return to user

It keeps looping until the LLM responds with plain text (no tool calls),
which means it's done working and ready to report back.
"""

import concurrent.futures
from .llm import LLM
from .tools import ALL_TOOLS, get_tool
from .tools.base import Tool
from .tools.agent import AgentTool
from .prompt import system_prompt
from .context import ContextManager
from .skills import Skill, skills_prompt_block


class Agent:
    def __init__(        # 初始化方法，用于创建和配置智能代理实例
        self,            # 实例引用
        llm: LLM,        # 语言模型参数，用于处理自然语言
        tools: list[Tool] | None = None,  # 工具列表，默认为None
        skills: list[Skill] | None = None,  # 技能列表，默认为None
        max_context_tokens: int = 128_000,  # 最大上下文标记数，默认为128,000
        max_rounds: int = 50,               # 最大交互轮数，默认为50
    ):
        self.llm = llm                      # 将传入的语言模型赋值给实例
        self.tools =tools if tools is not None else ALL_TOOLS  # 如果未提供工具，则使用默认工具集
         # 如果未提供工具，则使用默认工具集
        self.skills = skills if skills is not None else []      # 如果未提供技能，则使用空列表
        self.messages: list[dict] = []      # 初始化消息列表，用于存储对话历史
        self.context = ContextManager(max_tokens=max_context_tokens)  # 初始化上下文管理器
        self.max_rounds = max_rounds        # 设置最大交互轮数
        self._system = system_prompt(self.tools, skills_prompt_block(self.skills))  # 生成系统提示

        # wire up sub-agent capability  # 连接子代理能力
        for t in self.tools:               # 遍历所有工具
            if isinstance(t, AgentTool):    # 检查工具是否为AgentTool类型
                t._parent_agent = self    # 如果是，则设置其父代理为当前实例

    def _full_messages(self) -> list[dict]:
        return [{"role": "system", "content": self._system}] + self.messages

    def _tool_schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools]

    def chat(self, user_input: str, on_token=None, on_tool=None) -> str:
        """Process one user message. May involve multiple LLM/tool rounds."""
        self.messages.append({"role": "user", "content": user_input})
        self.context.maybe_compress(self.messages, self.llm)

        for _ in range(self.max_rounds):
            resp = self.llm.chat(
                messages=self._full_messages(),
                tools=self._tool_schemas(),
                on_token=on_token
            )

            # no tool calls -> LLM is done, return text
            if not resp.tool_calls:
                self.messages.append(resp.message)
                return resp.content

            # tool calls -> execute (parallel when multiple, like Claude Code's
            # StreamingToolExecutor which runs independent tools concurrently)
            self.messages.append(resp.message)

            if len(resp.tool_calls) == 1:
                tc = resp.tool_calls[0]
                if on_tool:
                    on_tool(tc.name, tc.arguments)
                result = self._exec_tool(tc)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
            else:
                # parallel execution for multiple tool calls
                results = self._exec_tools_parallel(resp.tool_calls, on_tool)
                for tc, result in zip(resp.tool_calls, results):
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

            # compress if tool outputs are big
            self.context.maybe_compress(self.messages, self.llm)

        return "(reached maximum tool-call rounds)"

    def chat_with_xml_tools(self, user_input: str, on_token=None, on_tool=None) -> str:
        """Process user message with XML-based tool calling for models that don't support native tools."""
        self.messages.append({"role": "user", "content": user_input})
        self.context.maybe_compress(self.messages, self.llm)

        for _ in range(self.max_rounds):#最多交互轮数,模型可能需要多次调用才能完成问题的解决
            resp = self.llm.chat(
                messages=self._full_messages(),
                on_token=on_token
            )

            # Check for XML tool calls in the content
            if resp.tool_calls:
                self.messages.append(resp.message)
                
                if len(resp.tool_calls) == 1:
                    tc = resp.tool_calls[0]
                    if on_tool:
                        on_tool(tc.name, tc.arguments)
                    result = self._exec_tool(tc)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                else:#多个工具的调用
                    results = self._exec_tools_parallel(resp.tool_calls, on_tool)
                    for tc, result in zip(resp.tool_calls, results):
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })
                
                self.context.maybe_compress(self.messages, self.llm)
            else:
                self.messages.append(resp.message)
                return resp.content

        return "(reached maximum tool-call rounds)"

    def _exec_tool(self, tc) -> str:
        """Execute a single tool call, returning the result string."""
        tool = get_tool(tc.name)
        if tool is None:
            return f"Error: unknown tool '{tc.name}'"
        try:
            return tool.execute(**tc.arguments)
        except TypeError as e:
            return f"Error: bad arguments for {tc.name}: {e}"
        except Exception as e:
            return f"Error executing {tc.name}: {e}"

#多线程执行，提高效率
    def _exec_tools_parallel(self, tool_calls, on_tool=None) -> list[str]:
        """Run multiple tool calls concurrently using threads.

        This is inspired by Claude Code's StreamingToolExecutor which starts
        executing tools while the model is still generating.  We simplify to:
        when the model returns N tool calls at once, run them in parallel.
        """
        for tc in tool_calls:
            if on_tool:
                on_tool(tc.name, tc.arguments)
#自动资源管理，线程池会自动回收线程资源
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(self._exec_tool, tc) for tc in tool_calls]
            return [f.result() for f in futures]

    def reset(self):
        """Clear conversation history."""
        self.messages.clear()
