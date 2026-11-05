# agentforge — Design

> This is the design as originally approved before implementation. The as-built architecture,
> including additions made during implementation (file-ownership guardrails, contract conformance
> probe, dependency allow-list, metrics), is in [ARCHITECTURE.md](ARCHITECTURE.md). Code lives under
> `src/agentforge/` rather than `agentforge/`, and the ambiguous example was changed to a greenfield
> requirement (see `examples/ambiguous/`).

An agentic software-engineering system that turns a software requirement into a
reviewable engineering outcome: normalized spec → task graph → design, code,
API contract, tests, docs → validation → structured engineering summary — with
human approval gates (controlled autonomy).

## 1. Key decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language | Python | Best LLM/agent ecosystem; Pydantic for typed, validated LLM outputs; pytest to execute generated tests. |
| LLM provider | Google Gemini (free tier, Flash family, e.g. Gemini 3.8 Flash) | Zero cost; strong enough for code generation when paired with a test-and-repair loop. Provider is pluggable behind an `LLMClient` interface. |
| Orchestration | Custom asyncio orchestrator (no LangGraph/CrewAI) | The task graph is produced at runtime by the Planner; a custom DAG executor handles that natively, and every orchestration behaviour (parallelism, recovery, gates, checkpoints) is explicit and defensible. |
| Human-in-the-loop | CLI gates (approve / edit / reject), `--auto-approve` for CI | Simple, reliable, scriptable. |
| Generated URL shortener stack | FastAPI + SQLite | Zero-setup, so the system can run the generated tests itself. Postgres + Redis documented as the scale-out path. |

## 2. Control flow

```
requirement text (+ optional --repo path for brownfield)
        │
        ▼
[1] Requirement Analyst ──► NormalizedSpec (goals, functional reqs, NFRs,
        │                                    ambiguities, assumptions, out-of-scope)
   ◆ GATE 1: human answers ambiguities or accepts default assumptions
        │
        ▼
[2] Codebase Analyst (brownfield only) ──► ImpactReport (modules, APIs, data flows touched)
        │
        ▼
[3] Planner ──► TaskGraph (tasks, dependencies, agent per task, acceptance criteria)
        │        └─ deterministic validation: acyclic, deps exist, every input has a producer
   ◆ GATE 2: human approves / edits / rejects with feedback (→ replan, max N)
        │
        ▼
[4] DAG executor (parallel where dependencies allow)
     Architect ──► design + OpenAPI contract + DB schema
        ├──► Code Gen ───┐
        ├──► Test Gen ───┼──► [5] Validator ──fail──► Reviewer attributes fault
        ├──► Docs ───────┤                              ├─► Code Gen repair (max 3 rounds)
        └──► Risk Analyst┘                              └─► Test Gen repair (if test is wrong)
        │
        ▼
   ◆ GATE 3: human reviews files, test results, risks → approve
        │
        ▼
[6] Final engineering summary (SUMMARY.md + run.json) + artifacts / diff
```

The system has two orchestration layers:

1. **Phase pipeline (static):** Analyze → Gate 1 → Impact analysis → Plan → Gate 2 →
   Execute → Validate → Gate 3 → Summarize.
2. **Task DAG executor (dynamic):** executes the Planner's task graph, running
   every task whose dependencies are satisfied concurrently.

### Cross-step coordination

- **Contract as single source of truth:** the Architect's OpenAPI contract feeds
  Code Gen and Test Gen independently and in parallel; the Validator then checks
  the generated app's actual OpenAPI spec against the designed contract.
- **Fault attribution:** on test failure the Reviewer decides whether the code or
  the test is wrong and routes the repair to the right agent.
- **Failure propagation:** a task that fails after retries causes its dependents
  to be skipped; the human chooses retry / replan / abort.
- **Shared blackboard state:** typed artifacts produced by one step are the
  inputs of the next; every step's inputs/outputs are recorded.

## 3. Components

| Layer | Contents |
|---|---|
| LLM layer | `LLMClient` interface; `GeminiClient` (official `google-genai` SDK, structured output validated with Pydantic); `ReplayClient` (record once, replay deterministically); `FakeClient` (unit tests). Rate limiter (RPM + concurrency cap) and retry with exponential backoff on 429/5xx. |
| Agents | Requirement Analyst, Codebase Analyst, Planner, Architect, Code Generator, Test Generator, Docs Writer, Risk Analyst, Reviewer. Each has a prompt file plus typed input/output models. |
| Orchestrator | Phase pipeline, DAG scheduler (asyncio), blackboard state, approval gates, checkpoint after every task, `resume <run_id>`. |
| Tools | Sandboxed file writer; code indexer (Python AST: modules, routes, imports); runners (`pytest`, `ruff` in subprocess with timeouts); OpenAPI contract checker. |

## 4. Error handling and guardrails

- **Schema-validated LLM output:** on validation failure the error is fed back to
  the model for self-correction (max 2 attempts), then the task fails cleanly.
- **Transient LLM errors:** retry with exponential backoff and jitter on 429/5xx.
- **Filesystem sandbox:** agents write only inside `runs/<run_id>/` (path-traversal
  checks). Brownfield mode never modifies the original repo — it works on a copy
  and emits a diff.
- **Command allowlist:** only `pytest` and `ruff`, always with timeouts.
- **Budgets:** per-run caps on LLM calls and tokens; caps on repair rounds and replans.
- **Audit log:** JSONL record of every LLM call (agent, prompt hash, tokens,
  latency, outcome) and every human decision.
- **Secrets:** API key from `.env` (git-ignored); secret-like files are never sent
  to the LLM.
- **Data policy:** Gemini free-tier content may be used by Google to improve its
  products, so only non-proprietary code is processed.

## 5. Example scenarios

| Scenario | Input |
|---|---|
| Greenfield (mandatory) | "Build a scalable URL shortener service with APIs, persistence, and analytics." |
| Brownfield | A small hand-written v1 URL shortener (`examples/brownfield/sample_repo`) without analytics and with a planted bug (duplicate custom alias returns 500 instead of 409). Requirement: "Add link expiration and fix the duplicate-alias bug." |
| Ambiguous | "Make the shortener production-ready and add some analytics." Expected to trigger heavy clarification at Gate 1. |

Each scenario ships with its input and a recorded run (task graph, logs,
artifacts, summary), so it can be reviewed without an API key or re-run live.

## 6. Testing approach

- **Unit:** DAG scheduler (ordering, parallelism, failure propagation), plan
  validation, sandbox, gates (auto-approve), retry/backoff, schema self-correction.
- **Integration:** full pipeline against `ReplayClient` — deterministic, runs in
  CI without an API key.
- **Output quality:** generated tests must pass, `ruff` clean, contract
  conformance, Reviewer rubric score.

## 7. Repository layout

```
agentforge/   llm/ agents/ orchestrator/ tools/ models/ prompts/ cli.py config.py
examples/     greenfield/ brownfield/sample_repo/ ambiguous/
tests/        unit/ integration/
docs/         DESIGN.md ARCHITECTURE.md TESTING.md
README.md  pyproject.toml  .env.example
```

## 8. Milestones

1. Foundation — models, LLM layer (Gemini + Replay + Fake), rate limiter, sandbox, config
2. Orchestrator — DAG executor, state, checkpoints, gates (unit-tested with fake agents)
3. Agents + prompts
4. Validation and repair loop
5. Brownfield — code indexer, impact analysis, sample repo, diff output
6. Run and record the three scenarios; final summary report
7. Documentation — README, ARCHITECTURE, TESTING
