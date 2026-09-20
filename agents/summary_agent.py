from pydantic import BaseModel


class SummaryAgentResult(BaseModel):
    status: str
    summary: str


def generate_daily_summary(
    conversation_history: str,
    trello_tasks: str,
) -> SummaryAgentResult:
    """
    Minimal deterministic Summary Agent.

    This version verifies that the Root Agent can invoke the Summary Agent
    and pass the required runtime context.
    """

    summary = (
        "Daily CNC Project Summary\n\n"
        f"Conversation activity:\n{conversation_history}\n\n"
        f"Current Trello tasks:\n{trello_tasks}"
    )

    return SummaryAgentResult(
        status="completed",
        summary=summary,
    )

if __name__ == "__main__":
    result = generate_daily_summary(
        conversation_history=(
            "TPTech reported that the prototype spindle was overheating.\n"
            "CFTech updated the cooling-system design."
        ),
        trello_tasks=(
            "Task: Revise the cooling-system design.\n"
            "Assignee: CFTech.\n"
            "Status: In Progress.\n"
            "Priority: High."
        ),
    )

    print(result.model_dump_json(indent=2))