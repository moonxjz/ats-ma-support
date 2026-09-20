
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
