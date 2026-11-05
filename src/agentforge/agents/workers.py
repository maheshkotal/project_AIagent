"""Worker agents that execute tasks of the plan, plus the router that dispatches tasks to them."""

from __future__ import annotations

from typing import Any, Protocol

from agentforge.agents.base import LLMAgent
from agentforge.agents.context import (
    REPO_OWNER,
    files_owned_by,
    find_design,
    is_source_path,
    is_test_path,
    render_design,
    render_files,
    render_other_tasks,
    render_spec,
    render_task,
    render_validation,
    retry_note,
    section,
    task_write_policy,
    write_files,
)
from agentforge.models import DesignDecision, DesignDoc, FileBundle, GeneratedFile, RiskReport, ValidationReport
from agentforge.orchestrator.dag import TaskContext
from agentforge.orchestrator.render import impact_markdown
from agentforge.orchestrator.state import RunState
from agentforge.tools.checks import CONTRACT_PATH
from agentforge.tools.sandbox import Sandbox


class Worker(Protocol):
    name: str

    async def run(self, ctx: TaskContext) -> dict[str, Any]: ...


def _always(_owner: str | None) -> bool:
    return True


class ArchitectAgent(LLMAgent):
    name = "architect"
    prompt_files = ("conventions", "architect")

    async def run(self, ctx: TaskContext) -> dict[str, Any]:
        state = ctx.state
        assert state.spec is not None
        parts = [section("Requirement", render_spec(state.spec)), section("Your task", render_task(ctx.task))]
        if state.impact is not None:
            parts.append(section("Impact analysis", impact_markdown(state.impact)))
        existing = files_owned_by(state, {REPO_OWNER}, is_source_path)
        if existing:
            parts.append(section("Existing code", render_files(ctx.workspace, existing)))
        parts.append(retry_note(ctx))
        design = await self.ask("\n".join(parts), DesignDoc)
        written = write_files(
            state,
            ctx.workspace,
            role="architect",
            owner=ctx.task.id,
            files=[
                GeneratedFile(path=CONTRACT_PATH, content=design.openapi_yaml),
                GeneratedFile(path="docs/architecture.md", content=design_markdown(design)),
            ],
            may_overwrite=task_write_policy(ctx),
        )
        return {"design": design.model_dump(mode="json"), "files": written}


class CoderAgent(LLMAgent):
    name = "coder"
    prompt_files = ("conventions", "coder")

    async def run(self, ctx: TaskContext) -> dict[str, Any]:
        state = ctx.state
        assert state.spec is not None and state.plan is not None
        ancestors = state.plan.ancestors(ctx.task.id)
        visible = files_owned_by(state, {*ancestors, REPO_OWNER}, is_source_path)
        prompt = "\n".join(
            [
                section("Requirement", render_spec(state.spec)),
                render_design(find_design(state, ancestors)),
                section("Your task", render_task(ctx.task)),
                section("Other tasks in the plan (do not write their files)", render_other_tasks(state, ctx.task)),
                section("Existing files you can import", render_files(ctx.workspace, visible)),
                retry_note(ctx),
            ]
        )
        bundle = await self.ask(prompt, FileBundle)
        written = write_files(
            state,
            ctx.workspace,
            role="coder",
            owner=ctx.task.id,
            files=bundle.files,
            may_overwrite=task_write_policy(ctx),
        )
        return {"summary": bundle.summary, "files": written}

    async def repair(
        self, state: RunState, workspace: Sandbox, report: ValidationReport, instructions: str, owner: str
    ) -> list[str]:
        assert state.spec is not None
        prompt = "\n".join(
            [
                section("Requirement", render_spec(state.spec)),
                render_design(find_design(state)),
                section("Validation results", render_validation(report)),
                section("Fix instructions from the reviewer", instructions),
                section(
                    "Current application files",
                    render_files(workspace, files_owned_by(state, set(state.file_owners.values()), is_source_path)),
                ),
            ]
        )
        bundle = await self.ask(prompt, FileBundle, purpose="repair")
        return write_files(state, workspace, role="coder", owner=owner, files=bundle.files, may_overwrite=_always)


class TesterAgent(LLMAgent):
    name = "tester"
    prompt_files = ("conventions", "tester")

    async def run(self, ctx: TaskContext) -> dict[str, Any]:
        state = ctx.state
        assert state.spec is not None and state.plan is not None
        ancestors = state.plan.ancestors(ctx.task.id)
        # Contract-first: testers see interfaces and the contract, never the implementation.
        prompt = "\n".join(
            [
                section("Requirement", render_spec(state.spec)),
                render_design(find_design(state, ancestors)),
                section("Your task", render_task(ctx.task)),
                section("Other tasks in the plan (do not write their files)", render_other_tasks(state, ctx.task)),
                section(
                    "Existing test files",
                    render_files(ctx.workspace, files_owned_by(state, {*ancestors, REPO_OWNER}, is_test_path)),
                ),
                retry_note(ctx),
            ]
        )
        bundle = await self.ask(prompt, FileBundle)
        written = write_files(
            state,
            ctx.workspace,
            role="tester",
            owner=ctx.task.id,
            files=bundle.files,
            may_overwrite=task_write_policy(ctx),
        )
        return {"summary": bundle.summary, "files": written}

    async def repair(
        self, state: RunState, workspace: Sandbox, report: ValidationReport, instructions: str, owner: str
    ) -> list[str]:
        assert state.spec is not None
        prompt = "\n".join(
            [
                render_design(find_design(state)),
                section("Validation results", render_validation(report)),
                section("Fix instructions from the reviewer", instructions),
                section(
                    "Current test files",
                    render_files(workspace, files_owned_by(state, set(state.file_owners.values()), is_test_path)),
                ),
            ]
        )
        bundle = await self.ask(prompt, FileBundle, purpose="repair")
        return write_files(state, workspace, role="tester", owner=owner, files=bundle.files, may_overwrite=_always)


class DocsAgent(LLMAgent):
    name = "docs"
    prompt_files = ("conventions", "docs")

    async def run(self, ctx: TaskContext) -> dict[str, Any]:
        state = ctx.state
        assert state.spec is not None and state.plan is not None
        design = find_design(state, state.plan.ancestors(ctx.task.id))
        prompt = "\n".join(
            [
                section("Requirement", render_spec(state.spec)),
                render_design(design),
                section("Architecture diagram (Mermaid)", design.architecture_diagram),
                section(
                    "Key decisions",
                    "\n".join(
                        f"- {d.decision}: {d.rationale} (alternatives: {_alternatives(d)})"
                        for d in design.key_decisions
                    ),
                ),
                section("Scaling strategy", design.scaling_strategy),
                section("Your task", render_task(ctx.task)),
                retry_note(ctx),
            ]
        )
        bundle = await self.ask(prompt, FileBundle)
        written = write_files(
            state,
            ctx.workspace,
            role="docs",
            owner=ctx.task.id,
            files=bundle.files,
            may_overwrite=task_write_policy(ctx),
        )
        return {"summary": bundle.summary, "files": written}


class RiskAgent(LLMAgent):
    name = "risk"
    prompt_files = ("risk",)

    async def run(self, ctx: TaskContext) -> dict[str, Any]:
        state = ctx.state
        assert state.spec is not None and state.plan is not None
        parts = [
            section("Requirement", render_spec(state.spec)),
            render_design(find_design(state, state.plan.ancestors(ctx.task.id)), include_contract=False),
            section("Your task", render_task(ctx.task)),
        ]
        if state.impact is not None:
            parts.append(section("Impact analysis", impact_markdown(state.impact)))
        parts.append(retry_note(ctx))
        report = await self.ask("\n".join(parts), RiskReport)
        written = write_files(
            state,
            ctx.workspace,
            role="risk",
            owner=ctx.task.id,
            files=[GeneratedFile(path="docs/RISKS.md", content=risk_markdown(report))],
            may_overwrite=task_write_policy(ctx),
        )
        return {"risk_report": report.model_dump(mode="json"), "files": written}


class TaskRouter:
    """The DAG executor's task handler: dispatches each task to the worker named by `task.agent`."""

    def __init__(self, workers: list[Worker]) -> None:
        self._workers = {w.name: w for w in workers}

    @property
    def agent_names(self) -> frozenset[str]:
        return frozenset(self._workers)

    async def __call__(self, ctx: TaskContext) -> dict[str, Any]:
        return await self._workers[ctx.task.agent].run(ctx)


# ---- markdown renderers for generated documents -------------------------------------------------


def _alternatives(decision: DesignDecision) -> str:
    return ", ".join(decision.alternatives_considered) or "none"


def design_markdown(design: DesignDoc) -> str:
    modules = "\n".join(
        f"### `{m.path}`\n\n{m.responsibility}\n\n```python\n" + "\n".join(m.public_interface) + "\n```\n"
        for m in design.modules
    )
    decisions = "\n".join(
        f"### {d.decision}\n\n{d.rationale}\n\n*Alternatives considered:* {_alternatives(d)}\n"
        for d in design.key_decisions
    )
    return (
        f"# Architecture\n\n{design.overview}\n\n```mermaid\n{design.architecture_diagram}\n```\n\n"
        f"## Modules\n\n{modules}\n## Data model\n\n```sql\n{design.data_model}\n```\n\n"
        f"## Key decisions\n\n{decisions}\n## Scaling strategy\n\n{design.scaling_strategy}\n\n"
        f"## API contract\n\nSee [`{CONTRACT_PATH}`](../{CONTRACT_PATH}).\n"
    )


def risk_markdown(report: RiskReport) -> str:
    risks = "\n".join(f"| {r.risk} | {r.likelihood.value} | {r.impact.value} | {r.mitigation} |" for r in report.risks)
    tradeoffs = "\n".join(
        f"### {t.decision}\n\n**Pros:** {'; '.join(t.pros)}\n\n**Cons:** {'; '.join(t.cons)}\n"
        for t in report.tradeoffs
    )
    failures = "\n".join(f"| {f.scenario} | {f.detection} | {f.handling} |" for f in report.failure_scenarios)
    return (
        "# Risks, trade-offs and validation\n\n## Risks\n\n| Risk | Likelihood | Impact | Mitigation |\n"
        f"|---|---|---|---|\n{risks}\n\n## Trade-offs\n\n{tradeoffs}\n"
        "## Failure scenarios\n\n| Scenario | Detection | Handling |\n|---|---|---|\n"
        f"{failures}\n\n## Validation strategy\n\n"
        + "\n".join(f"- {v}" for v in report.validation_strategy)
        + "\n\n## Assumptions and limitations\n\n"
        + "\n".join(f"- {a}" for a in report.assumptions_and_limitations)
        + "\n"
    )
