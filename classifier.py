import json
from enum import Enum
from ollama import chat
from pydantic import BaseModel, ConfigDict, Field, StrictBool


MODEL_NAME = "qwen3:8b"
# SYSTEM_PROMPT 完全==A.4.2 message classifier agent
SYSTEM_PROMPT = """
You are a specialized agent responsible for classifying Slack messages in a software development
team context.
Your goal is to accurately classify each message to determine if it represents:
1. Discussion of a new feature or bug that should be tracked as a task
2. Discussion related to an existing task
3. A response to an ongoing task creation workflow
4. Regular conversation that doesn’t require specific action
5. End-of-day activity that should trigger summary generation
ANALYSIS STEPS:
1. FIRST, determine if the message is directed to another team member rather than to the agent:
- Check for direct mentions using "@username" format
- Check if the message is clearly a response to another team member’s previous message
- If the message appears to be a conversation between team members, note this for response
handling later
2. Next, check if there is an active workflow. If there is and the message appears to be responding
to it, this message is likely a workflow response.
3. Extract key information from the message (username, timestamp, content).
4. Compare the message content against existing tasks in Trello.
5. Analyze the language patterns to identify discussion of new features/bugs.
6. Determine if it’s near end-of-day and relevant for summary generation.
CLASSIFICATION GUIDELINES:
- "WORKFLOW_RESPONSE" - PRIORITY CHECK: When there is an active workflow (especially task_creation)
and the message appears to be responding to it, even if it’s part of a cross-talk conversation.
This is most important during active workflows.
Examples: "Yes, create that task", "Add another label: backend", "Priority should be high"
- "NEW_TASK" - When users discuss implementing new features, fixing bugs, or creating improvements
that aren’t yet being tracked.
Classify as NEW_TASK only after confirming that no semantically equivalent task already exists in the current Trello task list.
IMPORTANT: These discussions may happen between team members and still need tracking, even if they’re not talking directly to you.
- "EXISTING_TASK" - When the requested or discussed work is already represented by a semantically equivalent task in the current Trello task list.
Before classifying a message as NEW_TASK, compare the requested work with all current Trello tasks by meaning, not only by exact wording. 
If an equivalent task already exists, classify the message as EXISTING_TASK with action "update_context", even if the message explicitly asks to "create a task" or "add a task".
Do not recommend creating a duplicate task.
Examples: "I’m working on the OAuth implementation", "The bug fix for user profiles is almost done"
- "REGULAR_CONVERSATION" - General discussion, questions, or conversation
not directly related to actionable tasks, OR cross-talk between team members
that doesn’t contain actionable task information.
Examples: "@john how’s the progress?", "Yes, I agree with Sarah",
"Let’s discuss this after the meeting"
- "SUMMARY_TRIGGER" - End-of-day messages or specific requests for summaries.
IMPORTANT NOTES ON CONVERSATION CONTEXT:
- Messages starting with "yes" or containing agreements could be either responses to you OR
to other team members - analyze carefully
- If a user is mentioned by name (with or without @ symbol), note this but still classify
the content appropriately
- Cross-talk often contains valuable information about tasks, progress, and blockers that
should still be tracked
- ADD A FLAG in your explanation when a message appears to be cross-talk but still contains
important information
For each message, return a JSON classification with:
1. "category": One of the above categories
2. "confidence": A score from 0.0-1.0 indicating your confidence
3. "explanation": Brief reasoning for this classification
4. "action": Recommended next action (create_task, update_context, continue_workflow, no_action,
generate_summary)
5. "is_cross_talk": Boolean (true/false) indicating if this appears to be a message between team members
not directed at the agent
Current team members:
<team_info>
{team_info}
</team_info>
Current time:
{_time}
Current tasks in Trello:
<trello_tasks>
{trello_tasks}
</trello_tasks>
Current workflow state:
<workflow_state>
{workflow_state}
</workflow_state>
Recent conversation history:
<conversation_history>
{conversation_history}
</conversation_history>

Return exactly one valid JSON object containing these fields:
- "category": one allowed category
- "confidence": a number from 0.0 to 1.0
- "explanation": brief reasoning based on the message and current context
- "action": one allowed action
- "is_cross_talk": true or false
Determine every field from the current message and supplied context.
Do not copy a classification from an example.

Output requirements:
- category must be one of:
  NEW_TASK, EXISTING_TASK, WORKFLOW_RESPONSE,
  REGULAR_CONVERSATION, SUMMARY_TRIGGER
- action must be one of:
  create_task, update_context, continue_workflow,
  no_action, generate_summary
- confidence must be a number between 0 and 1
- explanation must briefly justify the classification
- is_cross_talk must be true or false
- return only the JSON object
- do not include Markdown or additional commentary
""".strip()

def build_system_prompt(
    team_info: str,
    current_time: str,
    trello_tasks: str,
    workflow_state: str,
    conversation_history: str,
) -> str:
    return SYSTEM_PROMPT.format(
        team_info=team_info,
        _time=current_time,
        trello_tasks=trello_tasks,
        workflow_state=workflow_state,
        conversation_history=conversation_history,
    )

# 为pydantic 添加的内容

class Category(str, Enum):
    NEW_TASK = "NEW_TASK"
    EXISTING_TASK = "EXISTING_TASK"
    WORKFLOW_RESPONSE = "WORKFLOW_RESPONSE"
    REGULAR_CONVERSATION = "REGULAR_CONVERSATION"
    SUMMARY_TRIGGER = "SUMMARY_TRIGGER"


class Action(str, Enum):
    CREATE_TASK = "create_task"
    UPDATE_CONTEXT = "update_context"
    CONTINUE_WORKFLOW = "continue_workflow"
    NO_ACTION = "no_action"
    GENERATE_SUMMARY = "generate_summary"


class ClassifierResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str = Field(min_length=1)
    action: Action
    is_cross_talk: StrictBool

# end of 为Pydantic 添加的内容   
def classify_message(
    message: str,
    team_info: str,
    current_time: str,
    trello_tasks: str,
    workflow_state: str,
    conversation_history: str,
) -> ClassifierResult:
    system_prompt = build_system_prompt(
        team_info=team_info,
        current_time=current_time,
        trello_tasks=trello_tasks,
        workflow_state=workflow_state,
        conversation_history=conversation_history,
    )
    #print("\n===== FINAL SYSTEM PROMPT =====")
    #print(system_prompt)
    #print("===== END SYSTEM PROMPT =====\n")

    response = chat(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ],
        format=ClassifierResult.model_json_schema(),
        think=False,
    )

    return ClassifierResult.model_validate_json(
        response.message.content
    )


if __name__ == "__main__":
    test_message = (
     # NEW_TASK test message   
     #   "TPTech: Assistant, the spindle keeps overheating. "
     #  "Please create a task for CFTech to revise the "
     #   "cooling-system design by Friday."

     #EXISTING_TASK test message 1
     # "TPTech: Assistant, I’m working on the existing "
     # "'Revise the cooling-system design' task listed in Trello. "
     # "The spindle is still overheating, so please add this issue "
     # "to the task context."

     #EXISTING_TASK test message 2
     # "TPTech: Assistant, the spindle is still overheating. "
     # "Please update CFTech's cooling-system design task "
     # "with this latest test result."

     #CROSS TALK test message
     # "nice weather！"
     # "yeah, it's a sunny day!"
     # "@bob welcome back! how was your holidy?."

     #WORKFLOW_RESPONSE test message1
     # "TPTech: Set the priority to High."
     #WORKFLOW_RESPONSE test message2
     # "TPTech: High."

     #SUMMARY_TRIGGER test message
        "TPTech: Assistant, please generate today's end-of-day "
        "summary for the CNC machine project."
    )

    result = classify_message(
    message=test_message,
    team_info=(
        "TPTech: Internal ATS production technician\n"
        "CFTech: External CNC factory technician"
    ),
    current_time="2026-08-11 10:00 Australia/Melbourne",
    trello_tasks=(
    #
    "Existing task: Revise the cooling-system design.\n"
    "Assignee: CFTech.\n"
    "Status: In Progress.\n"
    "Due date: 2026-08-14."
    ),
    workflow_state=(
    # test without active workflow
       "No active workflow."
    # test with active workflow
      #  "Active workflow: task_creation\n"
      #  "Workflow status: awaiting_user_input\n"
      #  "Pending task title: Investigate spindle overheating\n"
      #  "Assigned to: CFTech\n"
      #  "Last question from Assistant: What priority should be assigned to this task?\n"
      #  "Required field awaiting response: priority"
    ),
    conversation_history=(
    # test history 1
    #    "TPTech previously reported that the prototype was undergoing testing."

    # test history 2
        #"TPTech asked the Assistant to create a task for CFTech to "
        #"investigate the spindle overheating issue.\n"
        #"Assistant asked: What priority should be assigned to this task?"
        # "The task priority was set to High."
    ),
    )

    print(result.model_dump_json(indent=2))