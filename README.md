# agentforge

An agentic software-engineering system that turns a plain-language requirement into a **reviewable
engineering outcome**: a normalized spec, a task graph, an architecture with an OpenAPI contract,
production-style code, unit and integration tests, documentation, a risk analysis, validation
results and a structured engineering summary. Humans approve at defined gates (controlled autonomy).

```
requirement ─► Analyst ─► [GATE spec] ─► Impact analysis* ─► Planner ─► [GATE plan]
           ─► DAG execution (architect → coder ∥ tester ∥ docs ∥ risk)
           ─► Validation ⇄ Review/Repair loop ─► [GATE output] ─► SUMMARY.md (+ changes.diff*)
                                                                    * brownfield only
```

- **Not a chatbot**: a checkpointed, resumable workflow with a dependency-aware parallel executor.
- **Contract-first**: code and tests are generated in parallel from the same design interfaces and
  OpenAPI contract; tests never see the implementation.
- **Deterministic validation**: syntax, dependency allow-list, lint, OpenAPI validity, live contract
  conformance, and the generated test suite, all run as real tools, not LLM opinions.
- **Self-repair with fault attribution**: a reviewer decides whether code, tests or both are wrong
  and routes fixes to the right agent; bounded rounds; fail-safe gates.
- **Guardrails**: sandboxed writes, role-based path rules, file-ownership checks between parallel
  agents, allow-listed execution with scrubbed environment, per-run LLM budgets, audit log.

## Assignment deliverables

| Deliverable | Where |
|---|---|
| Working prototype | `src/agentforge/` · CLI `agentforge` (see [Running](#running)) |
| URL shortener use case | `examples/greenfield/` |
| Architecture overview | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Example scenarios (greenfield / brownfield / ambiguous) | [`examples/`](examples/) |
| Setup instructions | [Setup](#setup) below |
| Testing approach, limitations, trade-offs | [`docs/TESTING.md`](docs/TESTING.md) |
| Original approved design | [`docs/DESIGN.md`](docs/DESIGN.md) |

## Setup

Requirements: macOS/Linux, [uv](https://docs.astral.sh/uv/) (installs Python 3.12 automatically).

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # if uv is not installed
uv sync                                            # create .venv with all dependencies
cp .env.example .env                               # then set GEMINI_API_KEY (Google AI Studio)
uv run pytest                                      # 72 tests, ~3 s, no API key needed
```

The API key is only needed for live runs. Tests and recorded example runs work offline.

## Running

```bash
# Greenfield (the mandatory use case), interactive approval gates
uv run agentforge run "Build a scalable URL shortener service with APIs, persistence, and analytics."

# Brownfield: change an existing codebase (the original is never modified)
uv run agentforge run "$(cat examples/brownfield/requirement.txt)" --repo examples/brownfield/sample_repo

# Ambiguous requirement: the analyst raises clarifying questions at the first gate
uv run agentforge run "$(cat examples/ambiguous/requirement.txt)"

# Non-interactive (CI): approve every gate with its default action
uv run agentforge run "..." --auto-approve

# Record a run's LLM responses, then replay it offline and deterministically
uv run agentforge run "..." --llm record --cassettes examples/greenfield/cassettes
uv run agentforge run "..." --llm replay --cassettes examples/greenfield/cassettes --auto-approve

# Resume an interrupted or aborted run (completed phases and tasks are not redone)
uv run agentforge resume <run-id>
```

At each gate you can **approve**, **reject** with feedback (the agents revise), or **abort**. At the
first gate you answer the analyst's open questions or press Enter to accept each default.

### What a run produces

```
runs/<run-id>/
  SUMMARY.md       structured engineering summary (plan, artifacts, validation, risks, limitations)
  changes.diff     unified diff against the original repository (brownfield)
  workspace/       the generated (or modified) project: app/, tests/, contract/, docs/, README.md
  baseline/        pristine copy of the original repository (brownfield)
  state.json       checkpointed run state (resume point)
  audit.jsonl      every LLM call, task transition, validation round and human decision
```

The generated service runs on its own from its workspace:

```bash
cd runs/<run-id>/workspace
uv run python -m pytest -q                       # its test suite
uv run uvicorn app.main:create_app --factory     # serve it on http://localhost:8000
```

## Configuration

Environment variables (or `.env`):

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | - | Google AI Studio key (live runs only) |
| `AGENTFORGE_MODEL` | `gemini-3.8-flash` | Gemini model id |
| `AGENTFORGE_RPM` | `10` | Client-side requests-per-minute limit |
| `AGENTFORGE_MAX_CONCURRENCY` | `3` | Max concurrent LLM calls / parallel tasks |
| `AGENTFORGE_MAX_LLM_CALLS` | `80` | Hard per-run LLM call budget |
| `AGENTFORGE_MAX_TOKENS` | `2000000` | Hard per-run token budget |
| `AGENTFORGE_RUNS_DIR` | `runs` | Where runs are stored |

## Project layout

```
src/agentforge/
  cli.py, factory.py      CLI and composition root
  orchestrator/           phase pipeline, DAG executor, gates, checkpoints, run state, summary
  agents/                 analyst, planner, impact, architect, coder, tester, docs, risk, reviewer
  prompts/                system prompts + shared target-stack conventions
  llm/                    provider interface, Gemini adapter, record/replay, fake, rate limit, retries
  tools/                  sandbox, allow-listed runner, validation checks, OpenAPI, code index, repo diff
  validation.py           workspace validator (runs checks concurrently)
tests/unit/               component tests (fake LLM)
tests/integration/        end-to-end runs with scripted LLM and real validation
examples/                 scenario inputs, brownfield sample repo, recorded outputs
docs/                     ARCHITECTURE.md, TESTING.md, DESIGN.md
```
