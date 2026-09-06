# 为了避免原型过早变复杂，初期制定一个明确约束：
#同一个 conversation_id 同一时间最多只能存在一个处于 awaiting_user_input 的交互式 Workflow。
#如果已经存在一个等待回复的 Workflow，又试图在同一会话启动另一个，系统应：要求先完成或取消现有 Workflow；或者要求创建新的 thread。
#
# 完整消息关联过程
# 收到消息
#→ 读取 conversation_id/thread_id
#→ 查询该会话的 active workflows
#→ 检查 reply_to_message_id
#→ 检查 sender/awaiting_response_from
#→ 得到唯一 workflow_id
#→ 将该 Workflow 传给 Classifier
#→ Classifier 判断消息意图
#→ Task Agent 更新该 workflow_id 对应的状态
#因此，接下来增加 conversation_id 并不仅是记录信息，它将成为多 Workflow 场景下最主要的消息关联键。
# workflow_id 负责唯一标识，conversation_id 负责把实际消息路由到正确的 Workflow。

from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field
from uuid import uuid4

def generate_workflow_id() -> str:
    """
    Generate a unique identifier for a workflow instance.
    """
    return f"WF-{uuid4().hex[:8].upper()}"
def current_utc_time() -> str:
    """
    Return the current UTC time in ISO 8601 format.
    """ 
    return datetime.now(timezone.utc).isoformat()

TaskField = Literal[
    "title",
    "assignee",
    "priority",
    "due_date",
]

WorkflowStatus = Literal[
    "awaiting_user_input",
    "ready_to_create",
    "completed",
    "cancelled",
]


class TaskCreationState(BaseModel):
    workflow_id: str = Field(
        default_factory=generate_workflow_id
    )
    workflow_type: Literal["task_creation"] = "task_creation"

    conversation_id: str
    initiator: str
    assigned_agent: Literal["task_agent"] = "task_agent"

    created_at: str = Field(
        default_factory=current_utc_time
    )
    updated_at: str = Field(
        default_factory=current_utc_time
    )

    status: WorkflowStatus = "awaiting_user_input"

    title: str | None = None
    assignee: str | None = None
    priority: str | None = None
    due_date: str | None = None

    missing_fields: list[TaskField] = Field(default_factory=list)
    pending_field: TaskField | None = None
    last_question: str | None = None
    source_message: str

    def refresh_state(self) -> None:
        """
        Recalculate missing fields and workflow status.
        """

        required_fields: list[TaskField] = [
            "title",
            "assignee",
            "priority",
            "due_date",
        ]

        self.missing_fields = [
            field_name
            for field_name in required_fields
            if not getattr(self, field_name)
        ]

        if self.missing_fields:
            self.status = "awaiting_user_input"
            self.pending_field = self.missing_fields[0]
        else:
            self.status = "ready_to_create"
            self.pending_field = None
            self.last_question = None
        self.updated_at = current_utc_time()


if __name__ == "__main__":
    state = TaskCreationState(
        conversation_id="CNC-THREAD-001",
        initiator="TPTech",
        title="Investigate spindle overheating",
        assignee="CFTech",
        source_message=(
            "TPTech: Please create a task for CFTech to investigate "
            "the spindle overheating issue."
        ),
    )

    state.refresh_state()

    print(state.model_dump_json(indent=2))