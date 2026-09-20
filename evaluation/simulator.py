"""Lightweight LLM Customer Simulator - Responds to order_agent based on scenario facts and strategy"""
import json
from pathlib import Path
# from ollama import chat
from tools.llm_client import chat
from agents.order_agent import OrderCreationState
from workflow.order.order_creation_extraction import ConversationMessage

MODEL_NAME = "qwen3:4b"
HISTORY_LIMIT = 5

class SimpleCustomerSimulator:
    """Generates customer responses based on scenario ground_truth and conversation_policy.
    
    Workflow:
    1. Load scenario ground_truth (customer info, configuration, address, etc.)
    2. Load conversation_policy (when to confirm, when to provide info, etc.)
    3. Determine what to do based on public_history
    4. Call LLM to generate responses that match scenario strategy
    """
    
    def __init__(self, scenario_path: str | Path):
        """
        Args:
            scenario_path: Path to scenario JSON file
        """
        self.scenario = self._load_scenario(scenario_path)
        self.description = self.scenario['customer']['description']
        self.ground_truth = self.scenario['customer']['ground_truth']
        self.policy = self.scenario['customer']['conversation_policy']
        self.initial_message = self.scenario['customer']['initial_message']
        self.initial_disclosures = set(self.scenario['customer']['initial_disclosures'])
        self.initially_known_fields = set(self.scenario['customer'].get('initially_known_configuration_fields', []))
    
    def _load_scenario(self, path: str | Path) -> dict:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _get_value(self, field: str) -> str | int | None:
        """Get field value from ground_truth"""
        if field.startswith('delivery_address.'):
            key = field.split('.', 1)[1]
            return self.ground_truth['delivery_address'].get(key)
        if field.startswith('customer.'):
            key = field.split('.', 1)[1]
            return self.ground_truth['customer'].get(key)
        if field in self.ground_truth['configuration']:
            return self.ground_truth['configuration'][field]
        if field == 'room_size':
            return self.ground_truth['room_size']
        return self.ground_truth['customer'].get(field)
    
    def _build_system_prompt(self) -> str:
        """Build system prompt with scenario facts and strategy"""
        config = self.ground_truth['configuration']
        customer = self.ground_truth['customer']
        address = self.ground_truth['delivery_address']
        
        return f"""You are a customer in conversation with an order agent. Please respond strictly according to the following facts and strategy.

        Scenario Description: {self.description}

## Your Real Information
- Name: {customer['customer_name']}
- Phone: {customer['phone']}
- Email: {customer['email']}
- Room Size: {self.ground_truth['room_size']}

## Delivery Address
- Address: {address['address']}
- City: {address['city']}
- State: {address['state']}
- Postcode: {address['postcode']}
- Country: {address['country']}

## Product Configuration You Want
- Product Model: {config['product_model']}
- Table Size: {config['table_size']}
- Timber: {config['timber']}
- Timber Painting: {config['timber_painting']}
- Felt Color: {config['felt_color']}
- Bracket: {config['bracket']}
- Top Profile: {config['top_profile']}
- Quantity: {config['quantity']}

## Conversation Strategy
{self._format_policy()}

## Important Rules
1. Follow the description in the scenario
2. Only provide information when explicitly requested
3. When confirming configuration, only confirm if you see the complete configuration list and it fully matches your requirements
4. When confirming the final order, only confirm if you see the complete order details and everything is correct
5. Do not proactively provide information that was not requested
6. Responses should be natural and concise, like a real customer"""
    
    def _format_policy(self) -> str:
        """Format conversation policy"""
        policy_parts = []
        
        config_confirm = self.policy.get('configuration_confirmation', {})
        if config_confirm:
            policy_parts.append("Configuration Confirmation Strategy:")
            if config_confirm.get('type') == 'CONFIRM_WITHOUT_CHANGE':
                response = config_confirm.get('response', {})
                if response and response.get('type') == 'EXACT_TEXT':
                    policy_parts.append(f"  - When Support asks you to confirm the configuration and it matches your requirements, reply exactly: '{response['text']}'")
                else:
                    policy_parts.append("  - If the configuration confirmation request includes the complete configuration and fully matches your requirements, reply: 'Yes, that configuration is correct.'")
                policy_parts.append("  - You must see the complete configuration list before confirming")
        
        final_confirm = self.policy.get('final_confirmation', {})
        if final_confirm:
            policy_parts.append("Final Order Confirmation Strategy:")
            behavior = final_confirm.get('type') or final_confirm.get('behavior')
            if behavior == 'CONFIRM_WITHOUT_CHANGE':
                response = final_confirm.get('response', {})
                if response and response.get('type') == 'EXACT_TEXT':
                    policy_parts.append(f"  - When Support asks you to confirm the final order and everything is correct, reply exactly: '{response['text']}'")
                else:
                    policy_parts.append("  - If the final order confirmation request includes complete order details and everything is correct, reply: 'Yes, I confirm the final order and would like to place it.'")
            elif behavior == 'REJECT_AND_ABANDON':
                customer_intent = final_confirm.get('customer_intent', 'ABANDON_PURCHASE')
                response = final_confirm.get('response', {})
                if response.get('type') == 'EXACT_TEXT':
                    policy_parts.append(f"  - When Support asks you to confirm the final order, you have changed your mind and want to cancel. Reply exactly: '{response['text']}'")
                else:
                    policy_parts.append("  - When Support asks you to confirm the final order, you have changed your mind. Politely decline and state that you don't want to proceed with the order anymore. Make it clear you are cancelling the entire order, not requesting changes.")
        
        subsequent = self.policy.get('subsequent_disclosure', '')
        if subsequent == 'ANSWER_REQUESTED_INFORMATION':
            policy_parts.append("Information Disclosure Strategy: Only provide customer info, delivery address, and other information when explicitly requested")
        
        return '\n'.join(policy_parts)
    
    def __call__(self, public_history: list[ConversationMessage] | None = None) -> str:
        """Generate the next customer message
        
        Args:
            public_history: Conversation history [{'role': 'user'|'assistant', 'content': '...'}]
        
        Returns:
            Customer's reply text
        """
        public_history = public_history or []
        
        if not public_history:
            return self.initial_message
        
        messages = [
            {"role": "system", "content": self._build_system_prompt()},
        ]
        
        for msg in public_history:
            messages.append({
                "role": "user" if msg.role == 'assistant' else "assistant",
                "content": msg.content
            })
        
        response = chat(
            model=MODEL_NAME,
            messages=messages,
            format=None,
            options={"temperature": 0.7}
        )
        
        return response.message.content.strip()

def create_simple_simulator(scenario_path: str | Path) -> SimpleCustomerSimulator:
    """Create lightweight simulator instance
    
    Args:
        scenario_path: Path to scenario JSON file
        llm_chat_fn: LLM invocation function
    
    Returns:
        SimpleCustomerSimulator instance
    """
    return SimpleCustomerSimulator(scenario_path)

def run_conversation(scenario_path: str | Path, order_agent_fn, support_agent_fn, max_turns: int = 20) -> list[dict]:
    """Run a complete conversation
    
    Args:
        scenario_path: Path to scenario JSON file
        order_agent_fn: Order agent function, receives customer_message and returns agent_response
        max_turns: Maximum number of conversation turns
    
    Returns:
        Complete conversation history
    """
    simulator = create_simple_simulator(scenario_path)
    history = []
    order_state = OrderCreationState(conversation_id="stage-7")
    
    for turn in range(max_turns):
        customer_message = simulator(history)
        history.append({"role": "user", "content": customer_message})
        print(f"\n[Customer Turn {turn+1}]\n{customer_message}")
        
        
        order_state, result = order_agent_fn(customer_message, history, order_state)
        agent_response = support_agent_fn(result, current_message=customer_message, conversation_history=history).text
        history.append({"role": "assistant", "content": agent_response})
        if len(history) > HISTORY_LIMIT:
            history.pop(0)
        print(f"\n[Agent Turn {turn+1}]\n{agent_response}")
    
    return history