# AgentForge Project Overview

## What this project does

AgentForge is a command-line software-engineering workflow. It takes a plain-language software
requirement and turns it into a reviewable engineering outcome: a normalized specification, a
dependency-aware task plan, architecture and an OpenAPI contract, generated code and tests,
documentation, risk analysis, validation results, and a final summary.

It is not a chatbot. A run is a checkpointed workflow with human approval gates. When working on
an existing repository (brownfield mode), AgentForge analyzes the code, works on a copy, and
produces a diff without modifying the original repository.

## End-to-end workflow

```text
Requirement
  -> Requirement analysis and clarification
  -> Human approval of the specification
  -> Optional brownfield impact analysis
  -> Task planning and human plan approval
  -> Dependency-aware execution by specialist agents
  -> Deterministic validation
  -> Review and bounded repair loop
  -> Human review of the output
  -> SUMMARY.md and, for brownfield runs, changes.diff
```

The planner's task graph lets independent work run in parallel. For example, after the architect
produces the design and contract, implementation, contract-first tests, documentation, and risk
analysis can be carried out as separate tasks. Validation runs real tools; it does not rely on an
LLM's opinion that the output is correct.

## Repository structure

```text
project root
├── src/agentforge/          AgentForge application package
│   ├── agents/              Agent implementations and prompt-context helpers
│   ├── llm/                 LLM provider interface, adapters, and safeguards
│   ├── models/              Validated data models shared across the workflow
│   ├── orchestrator/        Pipeline, task scheduler, gates, state, and reports
│   ├── prompts/             System prompts and generated-project conventions
│   ├── tools/               Sandbox, repository, runner, and contract utilities
│   ├── audit.py             Append-only run event log
│   ├── cli.py               Command-line entry point
│   ├── config.py            Environment-based settings
│   ├── factory.py           Application composition and dependency wiring
│   └── validation.py        Generated-workspace validation coordinator
├── tests/
│   ├── unit/                Tests for individual AgentForge components
│   └── integration/         End-to-end pipeline tests
├── examples/                Example requirements and a sample brownfield project
├── docs/                    Architecture, design, and testing documentation
├── README.md                Setup and usage guide
├── pyproject.toml           Package metadata, dependencies, and tool configuration
├── uv.lock                  Locked dependency versions
├── .python-version          Python version selection
└── .env.example             Template for optional LLM configuration
```

The `google-cloud-sdk/` directory present in the repository is a Google Cloud SDK distribution,
separate from the AgentForge application package.

## Application components

### CLI, configuration, and wiring

- **`src/agentforge/cli.py`** defines `agentforge run` and `agentforge resume`. It parses options,
  displays run progress, constructs the gate, and starts or resumes a pipeline.
- **`src/agentforge/config.py`** loads settings such as Gemini credentials, model selection,
  request limits, call/token budgets, retry limits, and the runs directory.
- **`src/agentforge/factory.py`** is the composition root. It constructs the LLM client stack,
  agents, validator, gates, checkpoint store, and pipeline.
- **`src/agentforge/audit.py`** writes run events to JSON Lines, including LLM calls, task
  transitions, validation rounds, and human decisions.

### Agents and prompts

The implementations live in **`src/agentforge/agents/`**:

- Requirement analyst: turns the request into a structured specification and surfaces important
  open questions.
- Impact analyst: for brownfield work, maps the existing repository and identifies affected code,
  APIs, data flows, and risks.
- Planner: decomposes work into tasks with dependencies and agent assignments.
- Architect: produces the design, module interfaces, data model, and API contract.
- Coder and tester: implement the application and write tests against the design and contract.
- Docs and risk agents: prepare project documentation and a design-specific risk assessment.
- Reviewer: diagnoses validation failures and attributes them to application code, tests, or both.

The shared **`LLMAgent`** in `agents/base.py` makes schema-validated LLM requests. Helpers in
`agents/context.py` format prompt context, render relevant files, and enforce role-specific write
permissions and file ownership rules.

The corresponding system instructions are in **`src/agentforge/prompts/`**. The prompts define
each agent's role, output expectations, and constraints; `conventions.md` provides common rules for
generated projects.

### Orchestration

The **`src/agentforge/orchestrator/`** package manages workflow execution:

- **`pipeline.py`** runs the fixed phases: analyze, approve the specification, optionally analyze
  repository impact, plan, execute, validate, and approve output.
- **`dag.py`** runs the planner's task graph. It schedules tasks once their dependencies succeed,
  caps parallel execution, retries eligible failures, and skips tasks blocked by failed
  prerequisites.
- **`state.py`** defines the shared, serializable run state, including the specification, plan,
  task results, file ownership, gate decisions, validation history, and metrics.
- **`gates.py`** provides interactive CLI, auto-approval, and scripted gates.
- **`checkpoint.py`** saves and restores run state so an interrupted run can resume.
- **`render.py`** formats specifications, plans, tasks, and validation results for humans.
- **`summary.py`** creates the final engineering summary from the run state and artifacts.

There are two levels of coordination: the pipeline controls the overall workflow, while the DAG
executor controls parallel tasks within the execution phase.

### Shared data models

The **`src/agentforge/models/`** package defines Pydantic models used to validate and pass data
between agents and workflow stages:

- **`spec.py`**: normalized requirements, scenario types, and ambiguity questions.
- **`impact.py`**: brownfield impact reports and impacted items.
- **`plan.py`**: tasks, dependencies, execution layers, and structural plan validation.
- **`artifacts.py`**: generated files, design documents, risk reports, and reviewer verdicts.
- **`validation.py`**: check results, test results, validation reports, and repair records.

### LLM integration

The **`src/agentforge/llm/`** package separates provider calls from the agents:

- **`base.py`** defines provider-independent request, response, error, and client interfaces.
- **`gemini.py`** adapts the Google Gemini API.
- **`structured.py`** requests structured JSON and validates responses against Pydantic schemas;
  invalid output is sent back for bounded self-correction.
- **`resilient.py`** adds per-run call/token budgets, retry with backoff, and audit events.
- **`ratelimit.py`** limits request rate and concurrent calls.
- **`replay.py`** records responses as cassettes or replays them for deterministic offline runs.
- **`fake.py`** provides a scripted in-memory client for tests.

### Tools, sandboxing, and validation

The **`src/agentforge/tools/`** package provides filesystem, repository, and validation utilities:

- **`sandbox.py`** confines reads and writes to a run's workspace, rejects paths escaping it, and
  blocks secret-like files.
- **`runner.py`** runs an allow-list of validation modules with a scrubbed environment and timeout.
- **`checks.py`** checks generated Python syntax, dependency imports, lint, OpenAPI validity and
  app/contract operation conformance, and generated tests.
- **`openapi.py`** parses and validates OpenAPI documents and extracts API operations.
- **`contract_probe.py`** loads a generated app in a subprocess to inspect its actual API
  operations.
- **`code_index.py`** uses Python's AST to map modules, symbols, routes, and imports for
  brownfield analysis.
- **`repo.py`** imports safe files from an existing repository and creates a unified diff.

**`src/agentforge/validation.py`** coordinates the checks. If validation fails, the reviewer
diagnoses the source of the failure, and the repair coordinator routes changes to the coder, tester,
or both. Repair rounds are bounded; unresolved failures are shown at the output approval gate.

## Tests and examples

- **`tests/unit/`** covers components such as the DAG executor, LLM layer, models, pipeline,
  sandbox, and agent guardrails.
- **`tests/integration/`** exercises full runs, including validation-and-repair and brownfield
  behavior.
- **`examples/greenfield/`** demonstrates building a URL shortener from a requirement.
- **`examples/brownfield/`** contains a requirement to change an existing sample URL shortener and
  the sample repository itself.
- **`examples/ambiguous/`** demonstrates asking for clarification before design and implementation.
- **`examples/README.md`** describes the example scenarios and their expected use.

The testing approach and known limitations are described in `docs/TESTING.md`.

## Documentation and project configuration

- **`docs/ARCHITECTURE.md`** describes the as-built architecture and implementation guardrails.
- **`docs/DESIGN.md`** records the original approved design.
- **`docs/TESTING.md`** explains testing of AgentForge and validation of generated projects.
- **`pyproject.toml`** declares the package, Python version, runtime and development dependencies,
  the `agentforge` console script, and pytest/Ruff settings.
- **`uv.lock`** pins dependency versions for repeatable installs.
- **`.env.example`** is a template for optional Gemini or Google Cloud credentials and settings;
  actual credentials belong in a local `.env`, not in source control.

## Run artifacts

Each run is stored under `runs/<run-id>/` by default:

```text
runs/<run-id>/
├── SUMMARY.md       Final engineering summary
├── changes.diff     Brownfield diff against the original repository (when applicable)
├── workspace/       Generated or modified project
├── baseline/        Pristine imported repository copy (brownfield only)
├── state.json       Checkpoint used to resume the run
└── audit.jsonl      Run events and decisions
```

In brownfield mode, the generated workspace is separate from the source repository. The baseline
copy is used to calculate `changes.diff`.

## Technology and safety notes

AgentForge itself is written for Python 3.12. It uses Pydantic for structured data, asyncio for
parallel task execution, and Gemini as the configured live LLM provider. The generated project
target conventions are separate from AgentForge's own implementation.

Guardrails include human approval gates, structured output validation, file-write and ownership
checks, an LLM call/token budget, command allow-listing, and timeouts. Generated code is executed
in a subprocess with a scrubbed environment; this is process-level isolation, not a container or
virtual machine. See `docs/TESTING.md` for the project's stated limitations and hardening ideas.

## Getting started

See `README.md` for complete installation and usage instructions. Typical commands are:

```bash
uv sync
uv run pytest
uv run agentforge run "Build a scalable URL shortener service with APIs, persistence, and analytics."
uv run agentforge resume <run-id>
```

Live runs need provider credentials. Tests and runs with recorded LLM cassettes can work offline.
