from ollama import chat

response = chat(
    model="qwen3:8b",
    messages=[
        {
            "role": "user",
            "content": (
                "Reply with exactly one valid JSON object and nothing else: "
                '{"status":"ready","model":"qwen3:8b"}'
            ),
        }
    ],
    think=False,
)

print(response.message.content)