from evaluation.simulator import create_simple_simulator
from agents.root_agent import execute_route
from entity.order_creation_state import OrderWorkflowStatus


def run_conversation(scenario_path: str, max_turns: int = 20, conversation_id: str = "test-001") -> list[dict]:
        """运行完整对话，集成 simulator 和 execute_route
        
        Args:
            max_turns: 最大对话轮次
            conversation_id: 对话 ID
        
        Returns:
            完整对话历史
        """
        simulator = create_simple_simulator(scenario_path)
        history = []
        state = None
        turn = 0
        
        while turn < max_turns:
            turn += 1
            
            customer_message = simulator(history)
            history.append({"role": "user", "content": customer_message})
            print(f"\n{'='*60}")
            print(f"[Customer Turn {turn}]")
            print(f"{'='*60}")
            print(customer_message)
            
            try:
                
                result = execute_route(
                    current_message=customer_message,
                    conversation_history=history,
                    conversation_id=conversation_id,
                    state=state
                )
                
                if result.executed and result.business_result:
                    state = result.state
                    agent_response = result.business_result.customer_response.text
                    history.append({"role": "assistant", "content": agent_response})
                    print(f"\n{'='*60}")
                    print(f"[Agent Turn {turn}]")
                    print(f"{'='*60}")
                    print(agent_response)
                elif result.support_result:
                    history.append({"role": "assistant", "content": "Support action processed"})
                    print(f"\n[Agent] Support action processed")
                else:
                    history.append({"role": "assistant", "content": "No response"})
                    print(f"\n[Agent] No response")
                if _is_terminal_confirmation(result.state.status):
                    print("\n[对话结束] 客户已确认订单")
                    break
                    
            except Exception as e:
                error_msg = f"Error: {str(e)}"
                history.append({"role": "assistant", "content": error_msg})
                print(f"\n[Error] {error_msg}")
                import traceback
                traceback.print_exc()

            
        
        return history
    
def _is_terminal_confirmation(status: OrderWorkflowStatus) -> bool:
    """检查是否是终止确认消息"""
    return status == OrderWorkflowStatus.COMPLETED or status == OrderWorkflowStatus.CANCELLED or status == OrderWorkflowStatus.FAILED


if __name__ == "__main__":
    history = run_conversation(
        scenario_path="evaluation/scenarios/S01.json",
        max_turns=20,
        conversation_id="test-001"
)