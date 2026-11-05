"""Agents that run before execution: requirement analysis and planning."""

from __future__ import annotations

from agentforge.agents.base import LLMAgent
from agentforge.agents.context import section
from agentforge.models import ImpactReport, NormalizedSpec, ScenarioType, TaskGraph
from agentforge.orchestrator.render import impact_markdown, spec_markdown


class RequirementAnalystAgent(LLMAgent):
    name = "analyst"
    prompt_files = ("analyst",)

    async def analyze(
        self, requirement: str, *, feedback: str | None = None, previous: NormalizedSpec | None = None
    ) -> NormalizedSpec:
        parts = [section("Requirement", requirement)]
        if previous is not None and feedback:
            parts.append(section("Your previous analysis", spec_markdown(previous)))
            parts.append(section("Reviewer feedback (address all of it)", feedback))
        return await self.ask("\n".join(parts), NormalizedSpec)


class PlannerAgent(LLMAgent):
    name = "planner"
    prompt_files = ("planner",)

    async def plan(
        self,
        spec: NormalizedSpec,
        impact: ImpactReport | None,
        *,
        feedback: str | None = None,
        previous: TaskGraph | None = None,
    ) -> TaskGraph:
        parts = [section("Normalized requirement", spec.model_dump_json(indent=2))]
        if impact is not None:
            parts.append(section("Impact analysis of the existing codebase", impact_markdown(impact)))
        if previous is not None and feedback:
            parts.append(section("Your previous plan", previous.model_dump_json(indent=2)))
            parts.append(section("Feedback on the previous plan (address all of it)", feedback))
        return await self.ask("\n".join(parts), TaskGraph)


def plan_rules(plan: TaskGraph, spec: NormalizedSpec) -> list[str]:
    """Domain rules on top of structural DAG validation: architect-first and contract-first testing."""
    errors: list[str] = []
    architects = [t for t in plan.tasks if t.agent == "architect"]
    if len(architects) != 1:
        errors.append(f"The plan must contain exactly one 'architect' task (found {len(architects)}).")
    else:
        architect = architects[0]
        if architect.depends_on:
            errors.append(f"The architect task '{architect.id}' must not depend on other tasks.")
        for task in plan.tasks:
            if task is not architect and architect.id not in plan.ancestors(task.id):
                errors.append(f"Task '{task.id}' must depend (directly or transitively) on '{architect.id}'.")
    agents = {t.agent for t in plan.tasks}
    if "tester" not in agents:
        errors.append("The plan must contain at least one 'tester' task.")
    if "coder" not in agents and spec.scenario is not ScenarioType.TESTS_DOCS:
        errors.append("The plan must contain at least one 'coder' task.")
    return errors
