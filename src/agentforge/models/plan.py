"""Task graph produced by the Planner and executed by the DAG executor."""

from __future__ import annotations

from collections.abc import Collection

from pydantic import BaseModel, Field


class Task(BaseModel):
    id: str = Field(description="Unique snake_case identifier, e.g. 'design_api'.")
    title: str
    agent: str = Field(description="Name of the agent that executes this task.")
    description: str = Field(description="What to produce, specific enough to act on without further context.")
    depends_on: list[str] = Field(default_factory=list, description="IDs of tasks whose outputs this task needs.")
    acceptance_criteria: list[str] = Field(default_factory=list)


class TaskGraph(BaseModel):
    rationale: str = Field(description="Why the work is decomposed and sequenced this way.")
    tasks: list[Task]

    def task(self, task_id: str) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)

    def ancestors(self, task_id: str) -> set[str]:
        """All tasks `task_id` transitively depends on."""
        seen: set[str] = set()
        stack = list(self.task(task_id).depends_on)
        while stack:
            current = stack.pop()
            if current not in seen:
                seen.add(current)
                stack.extend(self.task(current).depends_on)
        return seen

    def validation_errors(self, allowed_agents: Collection[str] | None = None) -> list[str]:
        """Deterministic structural checks; an LLM-produced plan is never trusted blindly."""
        errors: list[str] = []
        if not self.tasks:
            return ["The plan contains no tasks."]
        ids = [t.id for t in self.tasks]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            errors.append(f"Duplicate task ids: {', '.join(duplicates)}")
        known = set(ids)
        for task in self.tasks:
            if allowed_agents is not None and task.agent not in allowed_agents:
                errors.append(
                    f"Task '{task.id}' uses unknown agent '{task.agent}' (allowed: {', '.join(sorted(allowed_agents))})"
                )
            for dep in task.depends_on:
                if dep == task.id:
                    errors.append(f"Task '{task.id}' depends on itself")
                elif dep not in known:
                    errors.append(f"Task '{task.id}' depends on unknown task '{dep}'")
        if not errors:
            try:
                self.execution_layers()
            except ValueError as exc:
                errors.append(str(exc))
        return errors

    def execution_layers(self) -> list[list[str]]:
        """Group task ids into layers; tasks in the same layer can run in parallel (Kahn's algorithm)."""
        remaining = {t.id: set(t.depends_on) for t in self.tasks}
        layers: list[list[str]] = []
        while remaining:
            layer = sorted(tid for tid, deps in remaining.items() if not deps)
            if not layer:
                raise ValueError(f"Dependency cycle among tasks: {', '.join(sorted(remaining))}")
            layers.append(layer)
            for tid in layer:
                del remaining[tid]
            for deps in remaining.values():
                deps.difference_update(layer)
        return layers
