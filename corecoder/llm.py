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


def _clean_xml_tool_calls_from_content(content: str) -> str:
    """Remove XML tool call blocks from content, leaving only the actual message."""
    cleaned = re.sub(r'<function=[^>]+>.*?</function>', '', content, flags=re.DOTALL)
    cleaned = re.sub(r'\n\s*\n+', '\n\n', cleaned).strip()
    return cleaned


class LLM:
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None, temperature: float = 0.7, max_tokens: int = 4096):
        self.model = model
        self.client = OpenAI(api_key=api_key or "dummy", base_url=base_url)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None, on_token=None) -> LLMResponse:
        """Chat with the LLM, handling both OpenAI and local model formats."""
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            
            if tools:
                kwargs["tools"] = tools
            
            response = self.client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            message = choice.message
            
            if response.usage:
                self.total_prompt_tokens += response.usage.prompt_tokens
                self.total_completion_tokens += response.usage.completion_tokens
            
            content = message.content or ""
            prompt_tokens = response.usage.prompt_tokens if response.usage else 0
            completion_tokens = response.usage.completion_tokens if response.usage else 0
            tool_calls = []
            
            if message.tool_calls:
                for tc in message.tool_calls:
                    try:
                        arguments = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                    tool_calls.append(ToolCall(
                        id=tc.id,
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
        except APIError as e:
            print(f"API error: {e}")
            raise

    @property
    def estimated_cost(self):
        """Estimate cost based on token usage."""
        if self.model not in _PRICING:
            return None
        input_cost, output_cost = _PRICING[self.model]
        return (self.total_prompt_tokens * input_cost + self.total_completion_tokens * output_cost) / 1_000_000
