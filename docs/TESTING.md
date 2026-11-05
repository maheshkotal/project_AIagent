# Testing approach

There are two separate questions:

1. **Is the system correct?** Does the orchestration, guardrail and validation machinery behave as designed?
   Answered by this repository's own test suite (`uv run pytest`: 72 tests, about 3 s, no API key).
2. **Is a given run's output good?** Is the generated code correct and of reasonable quality? Answered at
   run time by the validation pipeline, the review/repair loop and the human gates.

## 1. Testing the system

### Strategy

- **The LLM is the only fake.** `FakeClient` returns scripted responses per agent. Everything else
  (file writes, sandbox, ownership rules, subprocesses, `pytest`, `ruff`, the contract probe) is real.
- **Determinism.** Time and sleeping are injected (fake clock, fake sleep, fixed jitter), so retry,
  backoff and rate-limit tests are exact and instantaneous.
- **Behaviour, not implementation.** Tests assert observable outcomes: run status, task states,
  files on disk, gate requests, audit events and what each agent was shown in its prompt.

### Unit tests (`tests/unit/`)

| Area | What is verified |
|---|---|
| DAG executor (`test_dag.py`) | Dependency order; parallel branches actually overlap; `max_parallel` respected; retry receives the previous error; failure skips transitive dependents while independent branches finish; non-retryable errors are not retried; resume skips succeeded tasks; checkpoint hook fires; cancellation stops in-flight tasks |
| Pipeline (`test_pipeline.py`) | All phases run and checkpoint; spec-gate answers become assumptions; spec rejection re-analyzes with feedback; invalid plans are replanned with the validation errors; human plan rejection replans with the feedback; replan limit aborts; abort at any gate; execution failure offers retry once and then defaults to abort; human can accept partial results; an aborted run resumes without redoing analysis, planning or succeeded tasks; brownfield import (secrets excluded, original untouched, diff and summary written); a summary is written even for aborted runs |
| LLM layer (`test_llm.py`) | Sliding-window rate limiter; retries with capped exponential backoff; permanent errors not retried; budget counts retries and blocks further calls; schema self-correction feeds validation errors back and gives up after N; record → replay offline; replay miss is non-retryable; Gemini adapter request mapping, token accounting and error mapping (429/5xx/transport → transient, 4xx → permanent, empty → transient) against a stubbed SDK |
| Plans and specs (`test_models.py`) | Execution layers; cycle, duplicate, self-dependency, unknown-dependency and unknown-agent detection; ambiguity resolution |
| Guardrails (`test_agents.py`, `test_sandbox.py`) | Role path rules; parallel tasks cannot overwrite each other but descendants can; a conflicting batch writes nothing; domain plan rules; dependency allow-list; syntax check; "no tests" is a failure; path traversal, absolute paths, symlink escape, secret files and size caps are rejected |

### Integration tests (`tests/integration/`)

| Test | Scenario |
|---|---|
| `test_full_pipeline_detects_bug_repairs_it_and_passes_validation` | Greenfield. The scripted coder ships a real FastAPI app with a planted bug (200 JSON instead of a 307 redirect). The contract-first tests, run by real `pytest`, catch it (3 pass, 1 fails); contract conformance passes; the reviewer attributes the fault to the code; the coder's repair prompt contains the failing test and the instructions; round 2 passes 4/4. Also asserts the tester never saw the implementation. |
| `test_unrepaired_failure_fails_safe_at_output_gate` | Same run with zero repair rounds: validation fails, the output gate defaults to abort, nothing is accepted silently. |
| `test_brownfield_fix_is_validated_and_diffed_without_touching_the_repo` | Brownfield against the real sample repo. The impact prompt contains the AST map (routes, import graph); the coder sees the code it modifies; the fix passes 8/8 (7 existing tests + the new regression test); `changes.diff` contains only changed files; the original repo is byte-for-byte unchanged. |

## 2. Validating generated output (every run)

### Validation pipeline (deterministic; static checks first, then dynamic checks in parallel)

| Check | Fails when |
|---|---|
| `syntax` | Any Python file does not parse, or no Python was generated |
| `dependencies` | Code imports anything outside the stdlib, local modules and the allow-list (`fastapi`, `starlette`, `pydantic`, `sqlalchemy`, `httpx`, `uvicorn`, `pytest`) |
| `lint` | `ruff` finds serious issues (syntax errors, undefined names); other findings are warnings |
| `contract` | `contract/openapi.yaml` is missing, not YAML, or not a valid OpenAPI document |
| `contract_conformance` | The app built by `create_app()` (in a subprocess) does not expose every operation in the contract; extra undocumented operations are warnings |
| `tests` | The generated suite fails, errors, times out, or collects no tests (results parsed per test from JUnit XML) |

### Quality controls beyond pass/fail

- **Contract-first tests**: written from interfaces and the contract without seeing the code, so they
  are an independent check rather than a mirror of the implementation.
- **Reviewer rules**: the contract and design are the arbiter; the reviewer must never weaken or
  delete tests that check contract behaviour to make them pass.
- **Schema validation of every agent output**, including OpenAPI validity at design time.
- **Plan validation**: structural DAG checks plus domain rules (architect first, contract-first testers).
- **Human gates**: the reviewer sees the validation results, repair history and files before approving.
- **Traceability**: `SUMMARY.md` and `audit.jsonl` show which task produced each file, every LLM call,
  every validation round and every decision.

### Verifying a run yourself

```bash
cd runs/<run-id>/workspace
uv run python -m pytest -q                         # the generated test suite (-m puts the workspace on sys.path)
uv run uvicorn app.main:create_app --factory       # run the service, then use the curl examples in its README
less ../SUMMARY.md ../changes.diff                 # report and (brownfield) change set
```

## 3. Known limitations and trade-offs

| Limitation | Impact | Mitigation / next step |
|---|---|---|
| Passing tests ≠ correct software | Validation proves consistency with the generated tests and contract, not completeness | Contract-first independence, human output gate; next: mutation testing, coverage thresholds |
| Process-level isolation for generated code | Malicious or buggy generated code runs as the user (scrubbed env, timeout, no secrets) | Container/VM sandbox (no network, read-only mounts) |
| LLM non-determinism | Two live runs differ | Record/replay cassettes make reviewed runs reproducible |
| Free-tier Gemini (Flash) | Lower single-shot quality; tight rate limits | Repair loop, rate limiter and backoff; provider is configurable |
| Free-tier data usage | Google may use prompts to improve products | Only non-proprietary code; use a paid tier for real codebases |
| Brownfield context budget (~200k chars) | Very large repositories don't fit | AST map is always included; next: retrieval over the index |
| Python/FastAPI target stack only | Other stacks are unsupported | Conventions, allow-list and probe are the only stack-specific parts |
| No load/performance testing of generated services | NFRs such as latency are designed for but not measured | Risk agent defines a load-test strategy; next: k6/locust stage |
| Contract conformance compares operations, not schemas | Response-shape drift is caught only by the generated tests | Next: schemathesis-style property testing against the contract |
| Fixed review/repair budget (3 rounds) | Hard problems may still fail validation | Fail-safe gate; human can send feedback for another round or resume later |
