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
    pre_model = os.getenv("MODELS_CN", "")

    if pre_model:
        model = pre_model
    
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
                "strict": True
            }
        }
        # 将格式指令添加到 system prompt
        format_instruction = (
            "Reply with exactly one valid JSON object and nothing else. "
            "You MUST include ALL fields from the schema. "
            "Do NOT simplify, truncate, or omit any fields. "
            "Return the complete JSON object with all required fields."
        ) + "\nJson_schema: " + json.dumps(format, ensure_ascii=False)
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] += "\n\n" + format_instruction
        else:
            messages.insert(0, {"role": "system", "content": format_instruction})
    else:
        params["response_format"] = {"type": "json_object"}

    
    response = client.chat.completions.create(**params)
    content = response.choices[0].message.content
    
    # 清理可能的 Markdown 代码块标记
    if content:
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
    
    return ChatResponse(content)