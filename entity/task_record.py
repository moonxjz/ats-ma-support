"""Persisted task record."""

from uuid import uuid4

from pydantic import BaseModel, Field

from entity.workflow_state import current_utc_time


def generate_task_id() -> str:
    return f"TASK-{uuid4().hex[:8].upper()}"


class TaskRecord(BaseModel):
    task_id: str = Field(
        default_factory=generate_task_id
    )

    source_workflow_id: str
    conversation_id: str
    created_by: str

    title: str
    assignee: str
    priority: str
    due_date: str

    status: str = "To Do"
    context_updates: list[str] = Field(
        default_factory=list
    )

    created_at: str = Field(
        default_factory=current_utc_time
    )
    updated_at: str = Field(
        default_factory=current_utc_time
    )
