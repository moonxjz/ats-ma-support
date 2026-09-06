import json
from pathlib import Path

from workflow_state import TaskCreationState


WORKFLOWS_FILE = Path(__file__).parent / "workflows.json"


def load_workflows() -> list[TaskCreationState]:
    """
    Load every stored workflow instance.
    """

    if not WORKFLOWS_FILE.exists():
        return []

    workflow_data = json.loads(
        WORKFLOWS_FILE.read_text(encoding="utf-8")
    )

    return [
        TaskCreationState.model_validate(item)
        for item in workflow_data
    ]


def save_workflows(
    workflows: list[TaskCreationState],
) -> None:
    """
    Save every workflow instance to workflows.json.
    """

    workflow_data = [
        workflow.model_dump()
        for workflow in workflows
    ]

    WORKFLOWS_FILE.write_text(
        json.dumps(
            workflow_data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def save_workflow(
    workflow_state: TaskCreationState,
) -> None:
    """
    Insert a new workflow or update an existing workflow
    with the same workflow_id.
    """

    workflows = load_workflows()

    for index, existing_workflow in enumerate(workflows):
        if (
            existing_workflow.workflow_id
            == workflow_state.workflow_id
        ):
            workflows[index] = workflow_state
            save_workflows(workflows)
            return

    workflows.append(workflow_state)
    save_workflows(workflows)


def load_workflow(
    workflow_id: str | None = None,
    conversation_id: str | None = None,
) -> TaskCreationState | None:
    """
    Load one workflow by workflow_id or conversation_id.

    Calling this function without an identifier is temporarily
    supported only when exactly one active workflow exists.
    """

    workflows = load_workflows()

    if workflow_id is not None:
        for workflow in workflows:
            if workflow.workflow_id == workflow_id:
                return workflow

        return None

    if conversation_id is not None:
        active_workflows = [
            workflow
            for workflow in workflows
            if (
                workflow.conversation_id == conversation_id
                and workflow.status
                not in {"completed", "cancelled"}
            )
        ]

        if len(active_workflows) == 0:
            return None

        if len(active_workflows) > 1:
            raise ValueError(
                "Multiple active workflows were found for "
                f"conversation {conversation_id}."
            )

        return active_workflows[0]

    active_workflows = [
        workflow
        for workflow in workflows
        if workflow.status not in {"completed", "cancelled"}
    ]

    if len(active_workflows) == 0:
        return None

    if len(active_workflows) > 1:
        raise ValueError(
            "Multiple active workflows exist. Provide workflow_id "
            "or conversation_id."
        )

    return active_workflows[0]

if __name__ == "__main__":
    workflows = load_workflows()

    if not workflows:
        print("No workflows were found.")
    else:
        first_workflow_id = workflows[0].workflow_id

        workflow_by_id = load_workflow(
            workflow_id=first_workflow_id
        )

        workflow_by_conversation = load_workflow(
            conversation_id="CNC-THREAD-002"
        )

        print("===== LOOKUP BY WORKFLOW ID =====")

        if workflow_by_id is None:
            print("Workflow not found.")
        else:
            print(workflow_by_id.model_dump_json(indent=2))

        print("\n===== LOOKUP BY CONVERSATION ID =====")

        if workflow_by_conversation is None:
            print("Workflow not found.")
        else:
            print(
                workflow_by_conversation.model_dump_json(
                    indent=2
                )
            )