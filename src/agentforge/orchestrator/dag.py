"""Dependency-aware task executor: runs every task whose dependencies are satisfied, concurrently."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from agentforge.audit import AuditLog
from agentforge.models import Task, TaskGraph
from agentforge.orchestrator.state import RunState, TaskResult, TaskStatus, utcnow
from agentforge.tools.sandbox import Sandbox


@dataclass(frozen=True)
class TaskContext:
    task: Task
    attempt: int
    # Outputs of this task's direct dependencies, keyed by task id (cross-step coordination).
    dependency_outputs: dict[str, dict[str, Any]]
    # Error from the previous attempt, so the agent can correct course on retry.
    previous_error: str | None
    state: RunState
    workspace: Sandbox


TaskHandler = Callable[[TaskContext], Awaitable[dict[str, Any] | BaseModel]]


class DagExecutor:
    def __init__(
        self,
        handler: TaskHandler,
        *,
        workspace: Sandbox,
        audit: AuditLog,
        max_attempts: int = 2,
        max_parallel: int = 4,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._handler = handler
        self._workspace = workspace
        self._audit = audit
        self._max_attempts = max_attempts
        self._max_parallel = max_parallel
        self._on_change = on_change or (lambda: None)

    async def run(self, graph: TaskGraph, state: RunState) -> dict[str, TaskResult]:
        """Execute `graph`, recording results in `state.tasks`.

        Tasks that already succeeded (e.g. in a resumed run) are not re-executed. A task that fails
        after its attempts are exhausted causes all transitive dependents to be skipped; independent
        branches keep running.
        """
        results = state.tasks
        for task in graph.tasks:
            previous = results.get(task.id)
            if previous is None or previous.status is not TaskStatus.SUCCEEDED:
                results[task.id] = TaskResult(task_id=task.id)

        running: dict[asyncio.Task[dict[str, Any]], str] = {}
        try:
            while True:
                self._skip_blocked(graph, results)
                for task in self._ready(graph, results)[: self._max_parallel - len(running)]:
                    running[asyncio.create_task(self._invoke(task, state))] = task.id
                if not running:
                    break
                done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for future in done:
                    self._settle(running.pop(future), future, results)
        finally:
            for future in running:
                future.cancel()
        return results

    def _ready(self, graph: TaskGraph, results: dict[str, TaskResult]) -> list[Task]:
        return [
            t
            for t in graph.tasks
            if results[t.id].status is TaskStatus.PENDING
            and all(results[d].status is TaskStatus.SUCCEEDED for d in t.depends_on)
        ]

    def _skip_blocked(self, graph: TaskGraph, results: dict[str, TaskResult]) -> None:
        # Iterate to a fixed point so skips propagate through chains of dependents.
        changed = True
        while changed:
            changed = False
            for task in graph.tasks:
                result = results[task.id]
                if result.status is not TaskStatus.PENDING:
                    continue
                blocked = [d for d in task.depends_on if results[d].status in (TaskStatus.FAILED, TaskStatus.SKIPPED)]
                if blocked:
                    result.status = TaskStatus.SKIPPED
                    result.error = f"Skipped: dependency {', '.join(blocked)} did not succeed"
                    self._audit.record("task_skipped", task_id=task.id, blocked_by=blocked)
                    changed = True
        self._on_change()

    async def _invoke(self, task: Task, state: RunState) -> dict[str, Any]:
        result = state.tasks[task.id]
        previous_error = result.error
        result.status = TaskStatus.RUNNING
        result.attempts += 1
        result.started_at = utcnow()
        self._audit.record("task_started", task_id=task.id, agent=task.agent, attempt=result.attempts)
        self._on_change()
        context = TaskContext(
            task=task,
            attempt=result.attempts,
            dependency_outputs=state.outputs_of(task.depends_on),
            previous_error=previous_error,
            state=state,
            workspace=self._workspace,
        )
        output = await self._handler(context)
        return output.model_dump(mode="json") if isinstance(output, BaseModel) else output

    def _settle(self, task_id: str, future: asyncio.Task[dict[str, Any]], results: dict[str, TaskResult]) -> None:
        result = results[task_id]
        result.finished_at = utcnow()
        exc = future.exception()
        if exc is None:
            result.status = TaskStatus.SUCCEEDED
            result.output = future.result()
            result.error = None
            self._audit.record("task_succeeded", task_id=task_id, attempts=result.attempts)
        else:
            result.error = f"{type(exc).__name__}: {exc}"
            retryable = getattr(exc, "retryable", True)
            if retryable and result.attempts < self._max_attempts:
                result.status = TaskStatus.PENDING  # picked up again on the next scheduling pass
                self._audit.record("task_retry", task_id=task_id, attempt=result.attempts, error=result.error)
            else:
                result.status = TaskStatus.FAILED
                self._audit.record("task_failed", task_id=task_id, attempts=result.attempts, error=result.error)
        self._on_change()
