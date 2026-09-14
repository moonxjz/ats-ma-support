from ollama import chat
from evaluation.simulator import run_conversation
from order_agent import process_order_creation_message
from support_agent import compose_customer_response


if __name__ == "__main__":
    history = run_conversation(
        scenario_path="evaluation/scenarios/S01.json",
        llm_chat_fn=chat,
        order_agent_fn=process_order_creation_message,
        support_agent_fn=compose_customer_response,
        max_turns=20
)