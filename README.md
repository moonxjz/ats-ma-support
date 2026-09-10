
=========================================
# DevNous Minimal Local Prototype

This repository contains a minimal local reproduction of the core workflow described in the DevNous multi-agent architecture.

The prototype focuses on message classification, root-agent routing, task-creation workflow management, local task persistence, and a simple summary agent.

## Current Architecture

The current prototype contains the following main components:

* `classifier.py`
  Classifies incoming messages and determines the corresponding action.

* `root_agent.py`
  Acts as the main routing agent and dispatches messages to the appropriate sub-agent or workflow.

* `task_agent.py`
  Handles task-creation and task-related workflows.

* `summary_agent.py`
  Generates a basic project summary.

* `workflow_state.py`
  Defines the task-creation workflow state.

* `workflow_store.py`
  Persists and retrieves multiple workflow instances.

* `task_store.py`
  Persists created tasks locally.

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

Individual components can then be tested directly, for example:

```bash
python classifier.py
python workflow_store.py
python task_store.py
python root_agent.py
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
