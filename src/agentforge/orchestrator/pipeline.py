"""Phase pipeline: the static outer loop of a run, with human gates between autonomous phases.

    analyze -> [gate: spec] -> impact (brownfield) -> plan -> [gate: plan] -> execute (DAG)
            -> [gate: output]

Every phase is checkpointed; resuming a run skips phases that already completed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agentforge.audit import AuditLog
from agentforge.config import Settings
from agentforge.models import ImpactReport, NormalizedSpec, TaskGraph
from agentforge.orchestrator.checkpoint import CheckpointStore
from agentforge.orchestrator.dag import DagExecutor, TaskHandler
from agentforge.orchestrator.gates import Gate, GateRequest
from agentforge.orchestrator.render import plan_markdown, spec_markdown, tasks_markdown
from agentforge.orchestrator.state import GateDecision, RunState, RunStatus, TaskStatus
from agentforge.tools.sandbox import Sandbox


class RequirementAnalyst(Protocol):
    async def analyze(self, requirement: str, *, feedback: str | None = None) -> NormalizedSpec: ...


class ImpactAnalyst(Protocol):
    async def analyze(self, spec: NormalizedSpec, repo_path: Path) -> ImpactReport: ...


class Planner(Protocol):
    async def plan(
        self,
        spec: NormalizedSpec,
        impact: ImpactReport | None,
        *,
        feedback: str | None = None,
        previous: TaskGraph | None = None,
    ) -> TaskGraph: ...


@dataclass(frozen=True)
class AgentSuite:
    analyst: RequirementAnalyst
    planner: Planner
    task_handler: TaskHandler
    # Agent names the task handler can execute; plans referencing anything else are rejected.
    worker_agents: Collection[str]
    impact_analyst: ImpactAnalyst | None = None


class RunAborted(Exception):
    pass


class Pipeline:
    PHASES = ("analyze", "approve_spec", "impact", "plan", "execute", "approve_output")

    def __init__(
        self,
        state: RunState,
        *,
        agents: AgentSuite,
        gate: Gate,
        store: CheckpointStore,
        audit: AuditLog,
        settings: Settings,
    ) -> None:
        self.state = state
        self.agents = agents
        self.gate = gate
        self.store = store
        self.audit = audit
        self.settings = settings
        self.workspace = Sandbox(store.run_dir(state.run_id) / "workspace")
        self._phases: dict[str, Callable[[], Awaitable[None]]] = {
            "analyze": self._analyze,
            "approve_spec": self._approve_spec,
            "impact": self._impact,
            "plan": self._plan,
            "execute": self._execute,
            "approve_output": self._approve_output,
        }

    async def run(self) -> RunState:
        state = self.state
        state.status, state.error = RunStatus.RUNNING, None
        self._save()
        for phase in self.PHASES:
            if phase in state.completed_phases:
                continue
            self.audit.record("phase_started", phase=phase)
            try:
                await self._phases[phase]()
            except RunAborted as exc:
                return self._finish(RunStatus.ABORTED, str(exc))
            except Exception as exc:  # the run records any failure instead of crashing the process
                return self._finish(RunStatus.FAILED, f"{phase}: {type(exc).__name__}: {exc}")
            state.completed_phases.append(phase)
            self.audit.record("phase_completed", phase=phase)
            self._save()
        return self._finish(RunStatus.COMPLETED)

    # ---- phases -------------------------------------------------------------------------------

    async def _analyze(self) -> None:
        self.state.spec = await self.agents.analyst.analyze(self.state.requirement)

    async def _approve_spec(self) -> None:
        assert self.state.spec is not None
        for revision in range(self.settings.max_replans + 1):
            spec = self.state.spec
            decision = await self._decide(
                GateRequest(
                    gate="spec",
                    title="Confirm requirement understanding",
                    summary=spec_markdown(spec),
                    questions=spec.ambiguities,
                )
            )
            if decision.action == "approve":
                self.state.spec = spec.resolve(decision.answers)
                return
            if revision < self.settings.max_replans:
                self.state.spec = await self.agents.analyst.analyze(
                    self.state.requirement, feedback=decision.feedback or "Re-examine the requirement."
                )
        raise RunAborted(f"Requirement understanding rejected {self.settings.max_replans + 1} times")

    async def _impact(self) -> None:
        if not self.state.repo_path:
            self.audit.record("phase_skipped", phase="impact", reason="greenfield run (no repository)")
            return
        if self.agents.impact_analyst is None:
            raise RuntimeError("Brownfield run requested but no impact analyst is configured")
        assert self.state.spec is not None
        self.state.impact = await self.agents.impact_analyst.analyze(self.state.spec, Path(self.state.repo_path))

    async def _plan(self) -> None:
        state = self.state
        assert state.spec is not None
        feedback: str | None = None
        while True:
            plan = await self.agents.planner.plan(state.spec, state.impact, feedback=feedback, previous=state.plan)
            state.plan = plan
            self._save()
            errors = plan.validation_errors(self.agents.worker_agents)
            if errors:
                self.audit.record("plan_invalid", errors=errors)
                feedback = "The previous plan failed automatic validation:\n" + "\n".join(f"- {e}" for e in errors)
            else:
                decision = await self._decide(
                    GateRequest(
                        gate="plan", title="Approve the execution plan", summary=plan_markdown(plan, state.impact)
                    )
                )
                if decision.action == "approve":
                    return
                feedback = decision.feedback or "The reviewer rejected the plan; propose a better decomposition."
            state.plan_revisions += 1
            if state.plan_revisions > self.settings.max_replans:
                raise RunAborted(f"No acceptable plan after {self.settings.max_replans} revisions")

    async def _execute(self) -> None:
        state = self.state
        assert state.plan is not None
        executor = DagExecutor(
            self.agents.task_handler,
            workspace=self.workspace,
            audit=self.audit,
            max_attempts=self.settings.task_max_attempts,
            max_parallel=self.settings.max_concurrency,
            on_change=self._save,
        )
        human_retries = 0
        while True:
            await executor.run(state.plan, state)
            incomplete = [r for r in state.tasks.values() if r.status is not TaskStatus.SUCCEEDED]
            if not incomplete:
                return
            can_retry = human_retries < self.settings.max_execution_retries
            decision = await self._decide(
                GateRequest(
                    gate="execution_failures",
                    title=f"{len(incomplete)} task(s) did not succeed",
                    summary=tasks_markdown(state)
                    + "\n\n*retry*: re-run failed and skipped tasks · *approve*: continue with partial results"
                    " · *abort*: stop the run",
                    # Fail safe: once retries are exhausted, the default (and auto) action is abort.
                    actions=["retry", "approve", "abort"] if can_retry else ["abort", "approve"],
                )
            )
            if decision.action == "approve":
                self.audit.record("partial_results_accepted", incomplete=[r.task_id for r in incomplete])
                return
            human_retries += 1

    async def _approve_output(self) -> None:
        await self._decide(
            GateRequest(
                gate="output",
                title="Review generated output",
                summary=tasks_markdown(self.state)
                + "\n\n**Workspace files**\n"
                + "\n".join(f"- `{f}`" for f in self.workspace.list_files()),
                actions=["approve", "abort"],
            )
        )

    # ---- helpers ------------------------------------------------------------------------------

    async def _decide(self, request: GateRequest) -> GateDecision:
        decision = await self.gate.review(request)
        self.state.decisions.append(decision)
        self.audit.record(
            "gate_decision",
            gate=decision.gate,
            action=decision.action,
            decided_by=decision.decided_by,
            feedback=decision.feedback,
            answers=decision.answers,
        )
        self._save()
        if decision.action == "abort":
            raise RunAborted(f"Aborted by {decision.decided_by} at gate '{decision.gate}'")
        return decision

    def _finish(self, status: RunStatus, error: str | None = None) -> RunState:
        self.state.status, self.state.error = status, error
        self.audit.record("run_finished", status=status.value, error=error)
        self._save()
        return self.state

    def _save(self) -> None:
        self.store.save(self.state)
