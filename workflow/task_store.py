#建立本地 Task Store 生成 TASKS_FILE ："tasks.json"。
# 这个本地存储暂时代替 Trello，同时保留 Workflow 与正式任务之间的追踪关系。
"""
TaskRecord.source_workflow_id
→ 该任务由哪个 Workflow 创建

TaskRecord.conversation_id
→ 该任务源自哪个 conversation

TaskRecord.created_by
→ 谁启动了任务创建
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from entity.workflow_state import TaskCreationState

from entity.task_record import TaskRecord

TASKS_FILE = Path(__file__).resolve().parents[1] / "tasks.json"

def current_utc_time() -> str:
    return datetime.now(timezone.utc).isoformat()

# 添加读取和保存函数
def load_tasks() -> list[TaskRecord]:
    """
    Load every locally stored task.
    """

    if not TASKS_FILE.exists():
        return []

    task_data = json.loads(
        TASKS_FILE.read_text(encoding="utf-8")
    )

    return [
        TaskRecord.model_validate(item)
        for item in task_data
    ]

def save_tasks(
    tasks: list[TaskRecord],
) -> None:
    """
    Save every task to tasks.json.
    """

    task_data = [
        task.model_dump()
        for task in tasks
    ]

    TASKS_FILE.write_text(
        json.dumps(
            task_data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
# 添加从 Workflow 创建任务的函数
def create_task_from_workflow(
    workflow_state: TaskCreationState,
) -> TaskRecord:
    """
    Create one persistent task from a ready workflow.
    """

    if workflow_state.status != "ready_to_create":
        raise ValueError(
            "The workflow is not ready to create a task."
        )

    required_values = {
        "title": workflow_state.title,
        "assignee": workflow_state.assignee,
        "priority": workflow_state.priority,
        "due_date": workflow_state.due_date,
    }

    missing_values = [
        field_name
        for field_name, field_value in required_values.items()
        if not field_value
    ]

    if missing_values:
        raise ValueError(
            "The workflow is missing required task fields: "
            + ", ".join(missing_values)
        )

    tasks = load_tasks()

    # Prevent duplicate task creation if the same Workflow is retried.
    for existing_task in tasks:
        if (
            existing_task.source_workflow_id
            == workflow_state.workflow_id
        ):
            return existing_task

    new_task = TaskRecord(
        source_workflow_id=workflow_state.workflow_id,
        conversation_id=workflow_state.conversation_id,
        created_by=workflow_state.initiator,
        title=workflow_state.title,
        assignee=workflow_state.assignee,
        priority=workflow_state.priority,
        due_date=workflow_state.due_date,
    )

    tasks.append(new_task)
    save_tasks(tasks)

    return new_task

# 独立测试
if __name__ == "__main__":
    test_workflow = TaskCreationState(
        workflow_id="WF-STORE-TEST-001",
        conversation_id="CNC-STORE-TEST-001",
        initiator="TPTech",
        title="Test local task persistence",
        assignee="CFTech",
        priority="High",
        due_date="2026-08-20",
        source_message=(
            "Create a test task for the local task store."
        ),
    )

    test_workflow.refresh_state()

    created_task = create_task_from_workflow(
        test_workflow
    )

    print("===== CREATED TASK =====")
    print(created_task.model_dump_json(indent=2))

    print("\n===== ALL TASKS =====")

    for task in load_tasks():
        print(task.model_dump_json(indent=2))