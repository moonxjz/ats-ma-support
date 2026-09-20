from pydantic import BaseModel
from entity.workflow_state import (
    TaskCreationState,
    current_utc_time,
)
from workflow.workflow_store import load_workflow, save_workflow
from workflow.task_store import (
    TaskRecord,
    create_task_from_workflow,
)

class TaskAgentResult(BaseModel):
    status: str
    operation: str
    message: str
    workflow_state: TaskCreationState | None = None
    created_task: TaskRecord | None = None

def start_task_creation(
    user_message: str,
    conversation_id: str,
    initiator: str,
    title: str | None = None,
    assignee: str | None = None,
    priority: str | None = None,
    due_date: str | None = None,
) -> TaskAgentResult:
    """
    Start a task-creation workflow using the fields already extracted
    from the user's message.
    """
    existing_workflow = load_workflow(
    conversation_id=conversation_id
    )

    if existing_workflow is not None:
        return TaskAgentResult(
            status=existing_workflow.status,
            operation="start_task_creation",
            message=(
                "An active workflow already exists for this conversation. "
                f"Workflow ID: {existing_workflow.workflow_id}"
            ),
            workflow_state=existing_workflow,
        )

    state = TaskCreationState(
        conversation_id=conversation_id,
        initiator=initiator,
        title=title,
        assignee=assignee,
        priority=priority,
        due_date=due_date,
        source_message=user_message,
    )

    state.refresh_state()

    question_by_field = {
        "title": "What title should be assigned to this task?",
        "assignee": "Who should be assigned to this task?",
        "priority": "What priority should be assigned to this task?",
        "due_date": "What due date should be assigned to this task?",
    }

    if state.pending_field is not None:
        state.last_question = question_by_field[state.pending_field]
        response_message = state.last_question
    else:
        response_message = (
            "All required task fields have been collected. "
            "The task is ready to be created."
        )

    save_workflow(state)

    return TaskAgentResult(
        status=state.status,
        operation="start_task_creation",
        message=response_message,
        workflow_state=state,
    )
def update_existing_task_context(
    user_message: str,
    trello_tasks: str,
) -> TaskAgentResult:
    """
    Minimal deterministic implementation for updating an existing task.

    This version verifies that the Task Agent receives the user message
    and the current Trello task context. It does not modify Trello yet.
    """

    return TaskAgentResult(
        status="completed",
        operation="update_existing_task_context",
        message=(
            "Existing task context update received. "
            f"Update request: {user_message} "
            f"Current task context: {trello_tasks}"
        ),
    )
def continue_active_workflow(
    user_message: str,
    conversation_id: str,
    workflow_state: TaskCreationState | None = None,
) -> TaskAgentResult:
    """
    Update the pending field of an active task-creation workflow.
    """
    if workflow_state is None:
        workflow_state = load_workflow(
            conversation_id=conversation_id
        )

    if workflow_state is None:
        return TaskAgentResult(
            status="no_active_workflow",
            operation="continue_active_workflow",
            message=(
                "No active task-creation workflow was found for "
                f"conversation {conversation_id}."
            ),
            workflow_state=None,
        )
    if workflow_state.conversation_id != conversation_id:
        return TaskAgentResult(
            status="conversation_mismatch",
            operation="continue_active_workflow",
            message=(
                "The supplied workflow does not belong to "
                f"conversation {conversation_id}."
            ),
            workflow_state=workflow_state,
        )
    
    if workflow_state.status != "awaiting_user_input":
        return TaskAgentResult(
            status=workflow_state.status,
            operation="continue_active_workflow",
            message="The workflow is not awaiting user input.",
            workflow_state=workflow_state,
        )

    if workflow_state.pending_field is None:
        return TaskAgentResult(
            status=workflow_state.status,
            operation="continue_active_workflow",
            message="The workflow has no pending field.",
            workflow_state=workflow_state,
        )

    # Remove an optional sender prefix such as "TPTech:"
    if ":" in user_message:
        field_value = user_message.split(":", 1)[1].strip()
    else:
        field_value = user_message.strip()

    # Remove a final full stop from short responses such as "High."
    field_value = field_value.rstrip(".")

    pending_field = workflow_state.pending_field

    setattr(
        workflow_state,
        pending_field,
        field_value,
    )

    workflow_state.refresh_state()

    question_by_field = {
        "title": "What title should be assigned to this task?",
        "assignee": "Who should be assigned to this task?",
        "priority": "What priority should be assigned to this task?",
        "due_date": "What due date should be assigned to this task?",
    }

    created_task: TaskRecord | None = None

    if workflow_state.pending_field is not None:
        workflow_state.last_question = question_by_field[
            workflow_state.pending_field
        ]
        response_message = workflow_state.last_question
    else:
        workflow_state.last_question = None

        created_task = create_task_from_workflow(
            workflow_state
        )

        workflow_state.status = "completed"
        workflow_state.updated_at = current_utc_time()

        response_message = (
            "Task created successfully. "
            f"Task ID: {created_task.task_id}"
    )

    save_workflow(workflow_state)

    return TaskAgentResult(
        status=workflow_state.status,
        operation="continue_active_workflow",
        message=response_message,
        workflow_state=workflow_state,
        created_task=created_task,
    )

if __name__ == "__main__":
    """
    result = start_task_creation(
        user_message=(
            "TPTech: Please create a task for CFTech to investigate "
            "the prototype's abnormal vibration."
        )
    )
    print(result.model_dump_json(indent=2))
    """
    """
    result = update_existing_task_context(
        user_message=(
            #"TPTech: Please update CFTech's cooling-system design task "
            #"with the latest spindle overheating test result."
        ),
        trello_tasks=(
            #"Task: Revise the cooling-system design.\n"
            #"Assignee: CFTech.\n"
            #"Status: In Progress.\n"
            #"Due date: 2026-08-14."
        ),
    )
    print(result.model_dump_json(indent=2))
    """
    """
    result = continue_active_workflow(
        user_message="TPTech: High.",
        workflow_state=(
            "Active workflow: task_creation\n"
            "Workflow status: awaiting_user_input\n"
            "Pending task title: Investigate spindle overheating\n"
            "Assigned to: CFTech\n"
            "Last question from Assistant: What priority should be assigned "
            "to this task?\n"
            "Required field awaiting response: priority"
        ),
    )
    print(result.model_dump_json(indent=2))
    """
    """
    result = start_task_creation(
        user_message=(
            "TPTech: Please create a task for CFTech to investigate "
            "the spindle overheating issue."
        ),
        title="Investigate spindle overheating",
        assignee="CFTech",
    )

    print(result.model_dump_json(indent=2))
    """
    """
    state = TaskCreationState(
        title="Investigate spindle overheating",
        assignee="CFTech",
        source_message=(
            "TPTech: Please create a task for CFTech to investigate "
            "the spindle overheating issue."
        ),
    )

    state.refresh_state()
    state.last_question = (
        "What priority should be assigned to this task?"
    )

    priority_result = continue_active_workflow(
        user_message="TPTech: High.",
        workflow_state=state,
    )

    print("===== AFTER PRIORITY RESPONSE =====")
    print(priority_result.model_dump_json(indent=2))

    due_date_result = continue_active_workflow(
        user_message="TPTech: 2026-08-20.",
        workflow_state=state,
    )

    print("\n===== AFTER DUE DATE RESPONSE =====")
    print(due_date_result.model_dump_json(indent=2))
    """
    result = continue_active_workflow(
        user_message=(
            "TPTech: The due date should be assigned "
            "to this task is 2026-08-20."
        ),
        conversation_id="CNC-THREAD-002",
    )

    print(result.model_dump_json(indent=2))