import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("KEY_CN"),
    base_url=os.getenv("ENDPOINT_CN"),
)

response = client.chat.completions.create(
    model=os.getenv("MODELS_CN", "qwen-plus"),
    messages=[
        {
            "role": "user",
            "content": (
                "Reply with exactly one valid JSON object and nothing else: "
                '{"status":"ready","model":"' + os.getenv("MODELS_CN", "qwen-plus") + '"}'
            ),
        }
    ],
    response_format={"type": "json_object"},
    temperature=0,
)

print(response.choices[0].message.content)