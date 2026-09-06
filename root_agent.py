from typing import Literal
from pydantic import BaseModel
from classifier import classify_message
from summary_agent import SummaryAgentResult, generate_daily_summary
from task_agent import (
    TaskAgentResult,
    continue_active_workflow,
    start_task_creation,
    update_existing_task_context,
)
from workflow_state import TaskCreationState
from workflow_store import load_workflow

AllowedAction = Literal[
    "create_task",
    "update_context",
    "continue_workflow",
    "no_action",
    "generate_summary",
]

class RoutingResult(BaseModel):
    action: AllowedAction
    target_agent: str | None
    operation: str
    executed: bool

class RootExecutionResult(BaseModel):
    routing: RoutingResult
    agent_result: SummaryAgentResult | TaskAgentResult | None

def route_action(action: AllowedAction) -> RoutingResult:
    """
    Route a classifier action to the appropriate downstream agent.

    This first version only returns the routing decision.
    It does not yet call a real Task Agent or Summary Agent.
    """

    if action == "create_task":
        return RoutingResult(
            action=action,
            target_agent="task_agent",
            operation="start_task_creation",
            executed=False,
        )

    if action == "update_context":
        return RoutingResult(
            action=action,
            target_agent="task_agent",
            operation="update_existing_task_context",
            executed=False,
        )

    if action == "continue_workflow":
        return RoutingResult(
            action=action,
            target_agent="task_agent",
            operation="continue_active_workflow",
            executed=False,
        )

    if action == "generate_summary":
        return RoutingResult(
            action=action,
            target_agent="summary_agent",
            operation="generate_daily_summary",
            executed=False,
        )

    if action == "no_action":
        return RoutingResult(
            action=action,
            target_agent=None,
            operation="do_nothing",
            executed=False,
        )

    raise ValueError(f"Unsupported action: {action}")

def execute_route(
    routing: RoutingResult,
    user_message: str,
    conversation_history: str,
    trello_tasks: str,
    workflow_state: TaskCreationState | None,
    conversation_id: str,
    initiator: str,
) -> RootExecutionResult:
    """
    Execute the routing decision by invoking the selected downstream agent.
    """
    if (
        routing.target_agent == "task_agent"
        and routing.operation == "start_task_creation"
    ):
        task_result = start_task_creation(
            user_message=user_message,
            conversation_id=conversation_id,
            initiator=initiator,
        )

        executed_routing = routing.model_copy(
            update={"executed": True}
        )

        return RootExecutionResult(
            routing=executed_routing,
            agent_result=task_result,
        )

    if (
        routing.target_agent == "task_agent"
        and routing.operation == "update_existing_task_context"
    ):
        task_result = update_existing_task_context(
            user_message=user_message,
            trello_tasks=trello_tasks,
        )

        executed_routing = routing.model_copy(
            update={"executed": True}
        )

        return RootExecutionResult(
            routing=executed_routing,
            agent_result=task_result,
        )
    if (
        routing.target_agent == "task_agent"
        and routing.operation == "continue_active_workflow"
    ):
        task_result = continue_active_workflow(
            user_message=user_message,
            conversation_id=conversation_id,
            workflow_state=workflow_state,
        )

        executed_routing = routing.model_copy(
            update={"executed": True}
    )

        return RootExecutionResult(
            routing=executed_routing,
            agent_result=task_result,
        )
    if (
        routing.target_agent == "summary_agent"
        and routing.operation == "generate_daily_summary"
    ):
        summary_result = generate_daily_summary(
            conversation_history=conversation_history,
            trello_tasks=trello_tasks,
        )

        executed_routing = routing.model_copy(
            update={"executed": True}
        )

        return RootExecutionResult(
            routing=executed_routing,
            agent_result=summary_result,
        )

    return RootExecutionResult(
        routing=routing,
        agent_result=None,
    )

if __name__ == "__main__":
    message_conversation_id = "CNC-THREAD-001"
    message_sender = "TPTech"

    test_message = (
        #"TPTech: High."
        #"CFTech: @TPTech, have you received the replacement bearing?"
        #"TPTech: the due date should be assigned to this task is 2026-08-20."
        "TPTech: The due date for this task is 2026-08-21."
    )
    conversation_history = (
    "TPTech asked the Assistant to create a task for CFTech to "
    "investigate spindle overheating.\n"
    "Assistant asked: What priority should be assigned to this task?\n"
    "TPTech answered: High.\n"
    "Assistant asked: What due date should be assigned to this task?"
    )
    trello_tasks = (
    "Existing task: Revise the cooling-system design.\n"
    "Assignee: CFTech.\n"
    "Status: In Progress.\n"
    "Priority: High.\n"
    "Due date: 2026-08-14."
    ) 
    """workflow_state = (
    #"Active workflow: task_creation\n"
    #"Workflow status: awaiting_user_input\n"
    #"Pending task title: Investigate spindle overheating\n"
    #"Assigned to: CFTech\n"
    #"Last question from Assistant: What priority should be assigned "
    #"to this task?\n"
    #"Required field awaiting response: priority"
    #"No active workflow."
    )"""

    # 1. 从 active_workflow.json 读取结构化状态
    active_workflow = load_workflow(
    conversation_id=message_conversation_id
    )

    # 2. 为 Classifier 准备文本版本
    if active_workflow is None:
        workflow_context = "No active workflow."
    else:
        workflow_context = active_workflow.model_dump_json(indent=2)

    classification = classify_message(
        message=test_message,
        team_info=(
            "TPTech: Internal ATS production technician\n"
            "CFTech: External CNC factory technician"
        ),
        current_time="2026-08-11 17:25 Australia/Melbourne",
        trello_tasks= trello_tasks,
        workflow_state= workflow_context,
        conversation_history=conversation_history,
    )

    routing = route_action(classification.action)

    execution = execute_route(
        routing=routing,
        user_message=test_message,
        conversation_id=message_conversation_id,
        initiator=message_sender,
        conversation_history=conversation_history,
        trello_tasks=trello_tasks,
        workflow_state=active_workflow,
    )

print("===== CLASSIFIER RESULT =====")
print(classification.model_dump_json(indent=2))

print("\n===== ROOT EXECUTION RESULT =====")
print(execution.model_dump_json(indent=2))
