目前我们已经完成了 **ATS Multi-Agent Support System 中 `ORDER_CREATE_WF` 的核心 MVP 到 Stage 8**。

整体进度可以最简化成：

```text
Customer Message + Ht + Wt
        ↓
Order Agent
        ↓
LLM Extraction
        ↓
Structured Update / Merge
        ↓
Workflow Controller
        ↓
COLLECT_REQUIREMENTS
        ↓
VALIDATE_CONFIGURATION
        ↓
CONFIGURATION_CONFIRMATION
```

目前完成的主要内容是：

1. **Workflow State / BusinessResult**

   * 建立 `OrderCreationState (Wt)`
   * 定义 workflow stage、status、tracking fields
   * 建立统一 `BusinessResult`

2. **COLLECT_REQUIREMENTS**

   * 判断 required customer information 是否完整
   * 缺信息 → `NEEDS_USER_INPUT`
   * 完整 → 自动进入 `VALIDATE_CONFIGURATION`

3. **VALIDATE_CONFIGURATION**

   * 当前 MVP 只验证：
     **table size + room size**
   * 使用 57-inch cue minimum-room-size rule
   * unsuitable → 要求客户修改 `table_size`
   * suitable → 进入 `CONFIGURATION_CONFIRMATION`

4. **Workflow Controller**

   * 已有真正的 controller execution loop
   * 根据 `current_stage` 调用对应 handler
   * `None` 表示内部继续执行
   * `BusinessResult` 表示 workflow 暂停并需要外部响应

5. **Structured State Update**

   * `ExtractedOrderInformation`
   * 可以安全地把 customer 更新 merge 到 Wt
   * required / optional / null / nested address 等规则已经处理

6. **LLM Information Extraction**

   * 使用 `qwen3:8b`
   * Input：
     **current message + Ht (conversation history) + Wt**
   * 能利用上下文理解：

     > “7ft or 8ft?” → “The bigger one.” → `8ft`

7. **Order Agent**

   * 已实现内部 orchestration：

```text
Extraction
   ↓
Merge
   ↓
Controller-owned re-entry
   ↓
Workflow Controller
```

8. **CONFIGURATION_CONFIRMATION + Multi-turn 修改**

   * 系统可以生成当前 configuration snapshot 并等待 customer confirmation
   * 如果 customer 改配置，例如：

```text
"I changed my mind. I want green felt."
```

系统会：

```text
CONFIGURATION_CONFIRMATION
        ↓
COLLECT_REQUIREMENTS
        ↓
更新 felt_color
        ↓
VALIDATE_CONFIGURATION
        ↓
CONFIGURATION_CONFIRMATION
```

也就是说，我们现在已经有了一个真正的 **multi-turn workflow loop**。

### 当前还没有做的

最重要的是：

```text
Customer: "Yes."
        ↓
确认当前 configuration
        ↓
PRICING
        ↓
...
        ↓
CREATE_ORDER
```

所以目前系统已经能够：

> **收集订单信息 → 理解上下文 → 更新 Wt → 验证 table/room configuration → 请求确认 → 客户修改后重新走 workflow。**

但还没有实现：

> **理解 Yes/No confirmation → Pricing → Final Confirmation → 真正 Create Order。**

当前最新版本是 **Stage 8 commit `3403ca7`**，Stage 8 测试结果为 **80 passed、2 skipped、0 failed**。

=========================================
# DevNous Minimal Local Prototype

This repository contains a minimal local reproduction of the core workflow described in the DevNous multi-agent architecture.

The prototype focuses on message classification, root-agent routing, task-creation workflow management, local task persistence, and a simple summary agent.

## Project Structure

The repository is organised into packages instead of a flat root directory:

```text
ats-ma-support/
├── main.py                     # CLI entry point (kept at the repository root)
├── agents/                     # one module per agent
│   ├── root_agent.py           # main routing agent
│   ├── order_agent.py          # ORDER_CREATE_WF orchestration
│   ├── support_agent.py        # enquiry / chat handling
│   ├── task_agent.py           # task-creation workflows
│   └── summary_agent.py        # basic project summary
├── entity/                     # domain entities and shared contracts
│   ├── business_result.py      # shared BusinessResult contract
│   ├── classification.py       # MessageCategory, ClassifierResult
│   ├── confirmation.py         # ConfirmationIntent, ConfirmationInterpretation
│   ├── conversation.py         # ConversationMessage, session and turn models
│   ├── extracted_order.py      # ExtractedOrderInformation / address
│   ├── order_creation_state.py # OrderCreationState, FinalOrderSnapshot
│   ├── order_record.py         # OrderCreationRecord and its identifiers
│   ├── routing.py              # routing and root execution results
│   ├── support.py              # Support value objects, knowledge contracts
│   ├── task_record.py          # TaskRecord
│   └── workflow_state.py       # TaskCreationState
├── workflow/                   # workflow orchestration and runtime
│   ├── classifier.py           # incoming message classification
│   ├── confirmation_presentation.py
│   ├── conversation_runtime.py # in-memory conversation runtime
│   ├── workflow_store.py       # multi-workflow persistence
│   ├── task_store.py           # local task persistence
│   └── order/                  # ORDER_CREATE_WF
│       ├── order_creation_catalog.py
│       ├── order_creation_confirmation.py
│       ├── order_creation_controller.py
│       ├── order_creation_extraction.py
│       ├── order_creation_order_store.py
│       ├── order_creation_rules.py
│       ├── order_creation_shipping.py
│       └── order_creation_updates.py
├── tools/                      # shared utilities
│   ├── knowledge_tool.py
│   └── llm_client.py
├── tests/                      # all test modules
├── evaluation/                 # benchmark contracts, fixtures and runner
├── data/                       # runtime fixtures (prices, rates, orders)
└── evidence/                   # recorded run evidence
```

`main.py` stays at the repository root so `python main.py` keeps working: running
a script puts its own directory (the repository root) on `sys.path`, which is
what the `agents.*` / `workflow.*` / `entity.*` / `tools.*` / `evaluation.*`
imports require.

## Workflow Management

Each task-creation workflow has its own:

* `workflow_id`
* `conversation_id`
* `initiator`
* workflow status
* timestamps
* task-related fields such as title, assignee, priority and due date

The system can retrieve the correct active workflow using either `workflow_id` or `conversation_id`.

Runtime workflow data is stored locally in:

```text
workflows.json
```

Created tasks are stored locally in:

```text
tasks.json
```

These runtime files are generated automatically and are excluded from Git version control.

## Requirements

* Python 3
* Ollama
* A locally available Ollama model
* Python dependencies listed in `requirements.txt`

Install Python dependencies with:

```bash
pip install -r requirements.txt
```

## Ollama

This prototype uses **Qwen3 8B** as the local LLM through Ollama.

Install Ollama separately, then download the required model:

```bash
ollama pull qwen3:8b


## Running the Prototype

Activate the Python virtual environment first.

Example on macOS:

```bash
source .venv/bin/activate
```

The CLI entry point stays at the repository root:

```bash
python main.py
```

Individual components can be executed as modules, for example:

```bash
python -m workflow.classifier
python -m workflow.workflow_store
python -m workflow.task_store
python -m agents.root_agent
```

Tests live in the `tests/` package and are run from the repository root:

```bash
python -m unittest tests.test_classifier -v
```

## Runtime Files

The following files are intentionally excluded from Git:

```text
.venv/
__pycache__/
active_workflow.json
workflows.json
tasks.json
```

`workflows.json` and `tasks.json` are created automatically when runtime state needs to be persisted.

## Current Scope

This repository is a minimal research prototype rather than a production-ready project-management system.

Currently implemented:

* message classification
* root-agent routing
* task-agent workflow handling
* multi-workflow persistence
* workflow lookup by workflow ID
* workflow lookup by conversation ID
* local task persistence
* duplicate task-creation protection
* basic summary generation

Not yet fully implemented:

* production project-management platform integration
* external task-management integration such as Trello
* persistent shared memory
* advanced summary workflows
* authentication and authorization
* production-grade error handling
* deployment infrastructure

## Purpose

The main purpose of this repository is to reproduce and study the architectural behaviour of DevNous and provide a foundation for further experimentation with multi-agent project-management systems.
