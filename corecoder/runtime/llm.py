"""LLM provider layer - thin wrapper over OpenAI-compatible APIs."""

import json
import re
import time
from dataclasses import dataclass, field

from openai import OpenAI, APIError, RateLimitError, APITimeoutError, APIConnectionError


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def message(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                for tc in self.tool_calls
            ]
        return msg

#模型定价表，单位是每百万tokens的美元价格，分别针对输入和输出tokens。请根据实际使用的模型和定价进行调整。
_PRICING = {
    "gpt-5.4": (2.5, 15), "gpt-5.4-mini": (0.75, 4.5), "gpt-5.4-nano": (0.2, 1.25),
    "o4-mini": (1.1, 4.4), "gpt-4.1": (2, 8), "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4), "gpt-4o": (2.5, 10), "gpt-4o-mini": (0.15, 0.6),
    "deepseek-chat": (0.27, 1.10), "deepseek-reasoner": (0.55, 2.19),
    "claude-opus-4-6": (5, 25), "claude-sonnet-4-6": (3, 15), "claude-haiku-4-5": (1, 5),
    "qwen3-max": (0.78, 3.9), "qwen3-plus": (0.26, 0.78), "qwen-max": (0.78, 3.9),
    "kimi-k2.5": (0.6, 3),
}


def _parse_xml_tool_calls(content: str) -> list[ToolCall]:
    """Parse XML-style tool calls like <function=name><parameter=key>value</parameter></function>"""
    tool_calls = []
    
    function_pattern = r'<function=([^>]+)>(.*?)</function>'
    function_matches = re.findall(function_pattern, content, re.DOTALL)
    
    for func_name, params_content in function_matches:
        tool_id = f"call_{len(tool_calls)}_{int(time.time() * 1000)}"
        arguments = {}
        
        param_pattern = r'<parameter=([^>]+)>(.*?)</parameter>'
        param_matches = re.findall(param_pattern, params_content, re.DOTALL)
        
        for key, value in param_matches:
            value = value.strip()
            try:
                arguments[key] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                arguments[key] = value
        
        if not arguments:
            simple_pattern = r'(\w+)=["\']?([^"\'\s>]+)["\']?'
            for key, value in re.findall(simple_pattern, params_content):
                try:
                    arguments[key] = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    arguments[key] = value
        
        if arguments:
            tool_calls.append(ToolCall(id=tool_id, name=func_name, arguments=arguments))
    
    return tool_calls

#清除格式问题
def _clean_xml_tool_calls_from_content(content: str) -> str:
    """Remove XML tool call blocks from content, leaving only the actual message."""
    cleaned = re.sub(r'<function=[^>]+>.*?</function>', '', content, flags=re.DOTALL)
    cleaned = re.sub(r'\n\s*\n+', '\n\n', cleaned).strip()
    return cleaned


class LLM:
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None, temperature: float = 0.7, max_tokens: int = 4096, timeout: int = 60):
        self.model = model
        self.client = OpenAI(api_key=api_key or "dummy", base_url=base_url, timeout=timeout)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None, on_token=None, tool_choice=None) -> LLMResponse:
        """Chat with the LLM, handling both OpenAI and local model formats."""
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            
            # Note: vLLM requires --enable-auto-tool-choice and --tool-call-parser
            # to support tools. Since your vLLM doesn't have these, we skip sending tools.
            # The model will still work but won't be able to call tools automatically.
            # if tools:
            #     kwargs["tools"] = tools
            
            # Always try streaming if on_token provided
            if on_token:
                kwargs["stream"] = True
                # Ask server to include usage in streaming chunks when supported.
                kwargs["stream_options"] = {"include_usage": True}
            #再次创建请求，子请求
            response = self.client.chat.completions.create(**kwargs)
            #流式输出
            if on_token and kwargs.get("stream"):
                # Streaming mode - handle both text and potential tool calls
                content = ""
                tool_calls = []
                tool_call_buffer = {}
                prompt_tokens = 0
                completion_tokens = 0
                
                for chunk in response:
                    # Usage may appear in a final chunk for compatible providers.
                    if hasattr(chunk, "usage") and chunk.usage:
                        prompt_tokens = chunk.usage.prompt_tokens or prompt_tokens
                        completion_tokens = chunk.usage.completion_tokens or completion_tokens

                    if not chunk.choices:
                        continue
                    
                    delta = chunk.choices[0].delta
                    if not delta:
                        continue
                    
                    # Check for tool calls in delta
                    if hasattr(delta, 'tool_calls') and delta.tool_calls:
                        for tc_chunk in delta.tool_calls:
                            if not hasattr(tc_chunk, 'index'):
                                continue
                            idx = tc_chunk.index
                            if idx not in tool_call_buffer:#空的，用于暂存
                                tool_call_buffer[idx] = {"id": "", "name": "", "arguments": ""}
                            
                            if hasattr(tc_chunk, 'id') and tc_chunk.id:
                                tool_call_buffer[idx]["id"] = tc_chunk.id
                            if hasattr(tc_chunk.function, 'name') and tc_chunk.function.name:
                                tool_call_buffer[idx]["name"] = tc_chunk.function.name
                            if hasattr(tc_chunk.function, 'arguments') and tc_chunk.function.arguments:
                                tool_call_buffer[idx]["arguments"] += tc_chunk.function.arguments#拼接分块的参数
                    
                    # Handle text content，处理文本内容
                    if hasattr(delta, 'content') and delta.content:
                        token = delta.content
                        content += token
                        on_token(token)
                
                # If we detected tool calls, parse them 解析工具调用
                if tool_call_buffer:
                    for idx, tc_data in sorted(tool_call_buffer.items()):
                        try:
                            arguments = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                        except json.JSONDecodeError:
                            arguments = {}
                        tool_calls.append(ToolCall(
                            id=tc_data["id"] or f"call_{idx}",
                            name=tc_data["name"],
                            arguments=arguments
                        ))
                
                # Update cumulative counters after streaming loop.
                self.total_prompt_tokens += prompt_tokens
                self.total_completion_tokens += completion_tokens
                #检测XML格式的内容
                # Check for XML-style tool calls in content
                if "<function=" in content or "<tool_call" in content:
                    parsed_tool_calls = _parse_xml_tool_calls(content)
                    content = _clean_xml_tool_calls_from_content(content)
                    tool_calls = parsed_tool_calls
                
                return LLMResponse(content, tool_calls, prompt_tokens, completion_tokens)
            else:
                # Non-streaming mode
                if hasattr(response, 'choices'):
                    choice = response.choices[0]
                    message = choice.message
                else:
                    message = response
                
                if hasattr(response, 'usage') and response.usage:
                    self.total_prompt_tokens += response.usage.prompt_tokens or 0
                    self.total_completion_tokens += response.usage.completion_tokens or 0
                
                content = message.content or "" if hasattr(message, 'content') else ""
                prompt_tokens = response.usage.prompt_tokens if hasattr(response, 'usage') and response.usage else 0
                completion_tokens = response.usage.completion_tokens if hasattr(response, 'usage') and response.usage else 0
                tool_calls = []
                
                if hasattr(message, 'tool_calls') and message.tool_calls:
                    for tc in message.tool_calls:
                        try:
                            arguments = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            arguments = {}
                        tool_calls.append(ToolCall(
                            id=tc.id if hasattr(tc, 'id') else f"call_{len(tool_calls)}",
                            name=tc.function.name,
                            arguments=arguments
                        ))
                
                if "<function=" in content or "<tool_call" in content:
                    parsed_tool_calls = _parse_xml_tool_calls(content)
                    content = _clean_xml_tool_calls_from_content(content)
                    tool_calls = parsed_tool_calls
                
                return LLMResponse(content, tool_calls, prompt_tokens, completion_tokens)
            
        except RateLimitError as e:
            print(f"Rate limit error: {e}")
            raise
        except APITimeoutError as e:
            print(f"Timeout error: {e}")
            raise
        except APIConnectionError as e:
            print(f"Connection error: {e}")
            raise
        except UnicodeDecodeError as e:
            raise RuntimeError(
                "检测到编码解码错误（可能是 Windows 终端 GBK 与 UTF-8 混用）。"
                "请先执行 chcp 65001 后重试。"
            ) from e
        except APIError as e:
            print(f"API error: {e}")
            raise
#进行匹配，输入和输出单价，计算总成本
    @property
    def estimated_cost(self):
        """Estimate cost based on token usage."""
        if self.model not in _PRICING:
            return None
        input_cost, output_cost = _PRICING[self.model]
        return (self.total_prompt_tokens * input_cost + self.total_completion_tokens * output_cost) / 1_000_000
