# Example scenarios

| Scenario | Input | Demonstrates |
|---|---|---|
| [`greenfield/`](greenfield/) | `Build a scalable URL shortener service with APIs, persistence, and analytics.` (the mandatory use case) | Full decomposition into a parallel task DAG; architecture + OpenAPI contract; layered code, contract-first unit and integration tests, docs and risk analysis; validation and repair |
| [`brownfield/`](brownfield/) | Add link expiry (410 Gone) and fix the duplicate-alias bug (500 → 409) in [`sample_repo/`](brownfield/sample_repo/) | Impact analysis from a static code map; changes to existing files only where needed; regression tests; `changes.diff`; original repo untouched |
| [`ambiguous/`](ambiguous/) | `Our marketing team needs short links with some stats. Make it fast and secure.` | Clarifying questions with options and defaults at the first gate; answers recorded as explicit assumptions that drive the design |

## Running them

```bash
uv run agentforge run "$(cat examples/greenfield/requirement.txt)"
uv run agentforge run "$(cat examples/brownfield/requirement.txt)" --repo examples/brownfield/sample_repo
uv run agentforge run "$(cat examples/ambiguous/requirement.txt)"
```

Add `--llm record --cassettes examples/<scenario>/cassettes` to record a live run, or
`--llm replay --cassettes examples/<scenario>/cassettes --auto-approve` to replay a recorded one offline.

Each recorded scenario directory contains:

- `requirement.txt`: the input
- `cassettes/`: recorded LLM responses (replayable offline)
- `output/SUMMARY.md`: the engineering summary of the recorded run
- `output/workspace/`: the generated project (brownfield: `output/changes.diff` too)

## The brownfield sample repository

`brownfield/sample_repo` is a small layered FastAPI + SQLAlchemy URL shortener (API → service →
repository → ORM) with its own OpenAPI contract and 7 passing tests. It has two known gaps the
brownfield requirement targets:

- `ShortenerService.shorten()` inserts a custom alias without checking whether it is taken, so the
  unique index raises `IntegrityError` and the API returns **500** instead of **409**.
- Links never expire.
