from __future__ import annotations

import asyncio

import pytest

from agentforge.audit import AuditLog
from agentforge.llm.base import BudgetExceededError
from agentforge.models import Task, TaskGraph
from agentforge.orchestrator.dag import DagExecutor, TaskContext
from agentforge.orchestrator.state import RunState, TaskStatus
from agentforge.tools.sandbox import Sandbox


def graph(*edges: tuple[str, list[str]]) -> TaskGraph:
    return TaskGraph(
        rationale="r",
        tasks=[Task(id=tid, title=tid, agent="coder", description="d", depends_on=deps) for tid, deps in edges],
    )


DIAMOND = graph(("design", []), ("code", ["design"]), ("tests", ["design"]), ("review", ["code", "tests"]))


class Recorder:
    """Task handler that records start order, concurrency and the context it received."""

    def __init__(self, fail: dict[str, list[Exception]] | None = None, delay: float = 0.01) -> None:
        self.fail = fail or {}
        self.delay = delay
        self.started: list[str] = []
        self.contexts: dict[str, TaskContext] = {}
        self.active = 0
        self.max_active = 0

    async def __call__(self, ctx: TaskContext) -> dict:
        self.started.append(ctx.task.id)
        self.contexts[ctx.task.id] = ctx
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            failures = self.fail.get(ctx.task.id)
            if failures:
                raise failures.pop(0)
            return {"produced_by": ctx.task.id, "inputs": sorted(ctx.dependency_outputs)}
        finally:
            self.active -= 1


def executor(handler, tmp_path, **kwargs) -> tuple[DagExecutor, list[dict]]:
    events: list[dict] = []
    audit = AuditLog()
    audit.subscribe(events.append)
    return DagExecutor(handler, workspace=Sandbox(tmp_path / "ws"), audit=audit, **kwargs), events


async def test_runs_in_dependency_order_with_parallel_branches(tmp_path) -> None:
    handler = Recorder()
    dag, _ = executor(handler, tmp_path)
    state = RunState(requirement="r")
    results = await dag.run(DIAMOND, state)

    assert all(r.status is TaskStatus.SUCCEEDED for r in results.values())
    assert handler.started[0] == "design" and handler.started[-1] == "review"
    assert handler.max_active == 2  # code and tests ran concurrently
    # Cross-step coordination: each task receives exactly its dependencies' outputs.
    assert results["review"].output["inputs"] == ["code", "tests"]
    assert handler.contexts["code"].dependency_outputs == {"design": results["design"].output}


async def test_respects_max_parallel(tmp_path) -> None:
    handler = Recorder()
    dag, _ = executor(handler, tmp_path, max_parallel=1)
    await dag.run(graph(("a", []), ("b", []), ("c", [])), RunState(requirement="r"))
    assert handler.max_active == 1


async def test_retries_with_previous_error_in_context(tmp_path) -> None:
    handler = Recorder(fail={"code": [RuntimeError("syntax error in main.py")]})
    dag, events = executor(handler, tmp_path, max_attempts=2)
    results = await dag.run(DIAMOND, RunState(requirement="r"))

    assert results["code"].status is TaskStatus.SUCCEEDED
    assert results["code"].attempts == 2
    assert handler.contexts["code"].attempt == 2
    assert "syntax error in main.py" in handler.contexts["code"].previous_error
    assert "task_retry" in [e["event"] for e in events]


async def test_failure_skips_dependents_but_not_independent_branches(tmp_path) -> None:
    handler = Recorder(fail={"code": [RuntimeError("boom")] * 2})
    dag, _ = executor(handler, tmp_path, max_attempts=2)
    results = await dag.run(DIAMOND, RunState(requirement="r"))

    assert results["code"].status is TaskStatus.FAILED
    assert results["tests"].status is TaskStatus.SUCCEEDED
    assert results["review"].status is TaskStatus.SKIPPED
    assert "code" in results["review"].error
    assert "review" not in handler.started


async def test_non_retryable_errors_fail_immediately(tmp_path) -> None:
    handler = Recorder(fail={"design": [BudgetExceededError("out of budget")]})
    dag, _ = executor(handler, tmp_path, max_attempts=3)
    results = await dag.run(DIAMOND, RunState(requirement="r"))
    assert results["design"].status is TaskStatus.FAILED
    assert results["design"].attempts == 1
    assert {results[t].status for t in ("code", "tests", "review")} == {TaskStatus.SKIPPED}


async def test_resume_skips_already_succeeded_tasks(tmp_path) -> None:
    state = RunState(requirement="r")
    first = Recorder(fail={"code": [RuntimeError("boom")] * 2})
    dag, _ = executor(first, tmp_path, max_attempts=2)
    await dag.run(DIAMOND, state)

    second = Recorder()
    dag, _ = executor(second, tmp_path, max_attempts=2)
    results = await dag.run(DIAMOND, state)
    assert sorted(second.started) == ["code", "review"]
    assert all(r.status is TaskStatus.SUCCEEDED for r in results.values())


async def test_checkpoint_hook_is_called_on_every_change(tmp_path) -> None:
    calls = 0

    def on_change() -> None:
        nonlocal calls
        calls += 1

    dag, _ = executor(Recorder(), tmp_path, on_change=on_change)
    await dag.run(graph(("a", [])), RunState(requirement="r"))
    assert calls >= 2  # at least: started, succeeded


async def test_cancellation_cancels_in_flight_tasks(tmp_path) -> None:
    handler = Recorder(delay=10)
    dag, _ = executor(handler, tmp_path)
    run = asyncio.create_task(dag.run(graph(("a", []), ("b", [])), RunState(requirement="r")))
    await asyncio.sleep(0.05)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    await asyncio.sleep(0)
    assert handler.active == 0
