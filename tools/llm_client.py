"""LLM 客户端封装 - 仿照 Ollama 接口，内部使用 OpenAI 兼容 API"""

import os
from dotenv import load_dotenv
from openai import OpenAI
import json

load_dotenv()

class ChatResponse:
    """仿照 Ollama 的响应结构"""
    class Message:
        def __init__(self, content: str):
            self.content = content

    def __init__(self, content: str):
        self.message = self.Message(content)


# Process-wide token-usage accumulator so a whole run / experiment can be summed.
# Every chat() call adds the prompt/completion tokens returned by the API.
_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}


def get_usage() -> dict:
    """Return a copy of the accumulated token usage so far."""
    return dict(_usage)


def reset_usage() -> None:
    """Zero out the accumulated token usage (call before starting a run)."""
    _usage["prompt_tokens"] = 0
    _usage["completion_tokens"] = 0
    _usage["total_tokens"] = 0
    _usage["calls"] = 0

def chat(
    model: str,
    messages: list[dict],
    format: dict | None = None,
    think: bool = False,
    options: dict | None = None,
) -> ChatResponse:
    """仿照 Ollama chat() 接口，内部使用 OpenAI 兼容 API
    
    参数:
        model: 模型名称
        messages: 消息列表 [{"role": "user/assistant/system", "content": "..."}]
        format: JSON Schema 格式约束（可选）
        think: 是否启用思考模式（OpenAI 不支持，忽略）
        options: 其他选项（如 temperature）
    
    返回:
        ChatResponse 对象，包含 message.content
    """
    api_key = os.getenv("KEY_CN", "")
    api_endpoint = os.getenv("ENDPOINT_CN", "https://www.dmxapi.cn/v1")
    
    client = OpenAI(api_key=api_key, base_url=api_endpoint)
    
    # 构建请求参数
    params = {
        "model": model,
        "messages": messages,
    }
    
    # 处理 temperature
    if options and "temperature" in options:
        params["temperature"] = options["temperature"]
    else:
        params["temperature"] = 0
    
    # 处理 JSON Schema 格式约束
    if format:
        params["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "output_schema",
                "schema": format,
                "strict": False
            }
        }

    
    response = client.chat.completions.create(**params)
    content = response.choices[0].message.content

    # Accumulate token usage if the endpoint reports it.
    usage = getattr(response, "usage", None)
    if usage is not None:
        _usage["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
        _usage["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0
        _usage["total_tokens"] += getattr(usage, "total_tokens", 0) or 0
        _usage["calls"] += 1

    return ChatResponse(content)