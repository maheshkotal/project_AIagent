from __future__ import annotations

from dataclasses import dataclass, field

from agentforge.audit import AuditLog
from agentforge.config import Settings
from agentforge.models import (
    Ambiguity,
    ChangeType,
    Impact,
    ImpactedItem,
    ImpactReport,
    NormalizedSpec,
    ScenarioType,
    Task,
    TaskGraph,
)
from agentforge.orchestrator import (
    AgentSuite,
    CheckpointStore,
    GateDecision,
    Pipeline,
    RunState,
    RunStatus,
    ScriptedGate,
    TaskContext,
    TaskStatus,
)

SPEC = NormalizedSpec(
    title="URL shortener",
    scenario=ScenarioType.GREENFIELD,
    problem_statement="Shorten URLs",
    functional_requirements=["POST /links"],
    non_functional_requirements=["p99 < 50ms"],
    ambiguities=[Ambiguity(id="A1", question="Custom aliases?", default_assumption="Yes", impact=Impact.MEDIUM)],
)

GOOD_PLAN = TaskGraph(
    rationale="contract first",
    tasks=[
        Task(id="design", title="Design", agent="architect", description="d"),
        Task(id="code", title="Code", agent="coder", description="d", depends_on=["design"]),
        Task(id="tests", title="Tests", agent="tester", description="d", depends_on=["design"]),
    ],
)

BAD_PLAN = TaskGraph(
    rationale="bad", tasks=[Task(id="code", title="Code", agent="wizard", description="d", depends_on=["x"])]
)


@dataclass
class FakeAnalyst:
    calls: list[str | None] = field(default_factory=list)

    async def analyze(self, requirement: str, *, feedback: str | None = None, previous=None) -> NormalizedSpec:
        self.calls.append(feedback)
        return SPEC


@dataclass
class FakePlanner:
    plans: list[TaskGraph]
    feedback: list[str | None] = field(default_factory=list)

    async def plan(self, spec, impact, *, feedback=None, previous=None) -> TaskGraph:
        self.feedback.append(feedback)
        return self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]


@dataclass
class FakeImpactAnalyst:
    seen_files: list[list[str]] = field(default_factory=list)

    async def analyze(self, spec, workspace) -> ImpactReport:
        self.seen_files.append(workspace.list_files())
        return ImpactReport(
            architecture_summary="FastAPI app",
            impacted_modules=[ImpactedItem(path="app/main.py", change_type=ChangeType.MODIFY, reason="add expiry")],
        )


@dataclass
class FakeWorkers:
    fail: set[str] = field(default_factory=set)
    runs: list[str] = field(default_factory=list)

    async def __call__(self, ctx: TaskContext) -> dict:
        self.runs.append(ctx.task.id)
        if ctx.task.id in self.fail:
            raise RuntimeError(f"{ctx.task.id} failed")
        ctx.workspace.write_text(f"{ctx.task.id}.txt", "done")
        return {"ok": True}


def make_pipeline(
    tmp_path,
    *,
    gate: ScriptedGate | None = None,
    planner: FakePlanner | None = None,
    workers: FakeWorkers | None = None,
    state: RunState | None = None,
    impact_analyst: FakeImpactAnalyst | None = None,
) -> tuple[Pipeline, list[dict]]:
    events: list[dict] = []
    audit = AuditLog()
    audit.subscribe(events.append)
    agents = AgentSuite(
        analyst=FakeAnalyst(),
        planner=planner or FakePlanner([GOOD_PLAN]),
        task_handler=workers or FakeWorkers(),
        worker_agents={"architect", "coder", "tester"},
        impact_analyst=impact_analyst,
    )
    pipeline = Pipeline(
        state or RunState(requirement="Build a URL shortener"),
        agents=agents,
        gate=gate or ScriptedGate(),
        store=CheckpointStore(tmp_path / "runs"),
        audit=audit,
        settings=Settings(max_replans=2, task_max_attempts=1, max_execution_retries=1),
    )
    return pipeline, events


async def test_happy_path_runs_all_phases_and_checkpoints(tmp_path) -> None:
    pipeline, events = make_pipeline(tmp_path)
    state = await pipeline.run()

    assert state.status is RunStatus.COMPLETED, state.error
    assert state.completed_phases == list(Pipeline.PHASES)
    assert {r.status for r in state.tasks.values()} == {TaskStatus.SUCCEEDED}
    assert [d.gate for d in state.decisions] == ["spec", "plan", "output"]
    assert pipeline.workspace.list_files() == ["code.txt", "design.txt", "tests.txt"]
    reloaded = CheckpointStore(tmp_path / "runs").load(state.run_id)
    assert reloaded.status is RunStatus.COMPLETED
    assert any(e["event"] == "phase_skipped" and e["phase"] == "impact" for e in events)


async def test_spec_gate_answers_become_assumptions(tmp_path) -> None:
    gate = ScriptedGate([GateDecision(gate="spec", action="approve", answers={"A1": "No"})])
    pipeline, _ = make_pipeline(tmp_path, gate=gate)
    state = await pipeline.run()
    assert state.spec.ambiguities == []
    assert "Custom aliases? -> No" in state.spec.assumptions
    assert gate.requests[0].questions[0].id == "A1"


async def test_spec_rejection_reanalyzes_with_feedback(tmp_path) -> None:
    gate = ScriptedGate([GateDecision(gate="spec", action="reject", feedback="Analytics must be real-time")])
    pipeline, _ = make_pipeline(tmp_path, gate=gate)
    state = await pipeline.run()
    assert state.status is RunStatus.COMPLETED
    assert pipeline.agents.analyst.calls == [None, "Analytics must be real-time"]


async def test_invalid_plan_is_replanned_with_validation_errors(tmp_path) -> None:
    planner = FakePlanner([BAD_PLAN, GOOD_PLAN])
    pipeline, events = make_pipeline(tmp_path, planner=planner)
    state = await pipeline.run()

    assert state.status is RunStatus.COMPLETED
    assert state.plan_revisions == 1
    assert "unknown agent 'wizard'" in planner.feedback[1]
    assert any(e["event"] == "plan_invalid" for e in events)


async def test_rejected_plan_is_replanned_with_human_feedback(tmp_path) -> None:
    gate = ScriptedGate([GateDecision(gate="plan", action="reject", feedback="Add a load-test task")])
    planner = FakePlanner([GOOD_PLAN])
    pipeline, _ = make_pipeline(tmp_path, gate=gate, planner=planner)
    state = await pipeline.run()
    assert state.status is RunStatus.COMPLETED
    assert planner.feedback == [None, "Add a load-test task"]


async def test_replan_limit_aborts_run(tmp_path) -> None:
    pipeline, _ = make_pipeline(tmp_path, planner=FakePlanner([BAD_PLAN]))
    state = await pipeline.run()
    assert state.status is RunStatus.ABORTED
    assert "No acceptable plan" in state.error
    assert "execute" not in state.completed_phases


async def test_abort_at_gate_stops_run(tmp_path) -> None:
    pipeline, _ = make_pipeline(tmp_path, gate=ScriptedGate([GateDecision(gate="plan", action="abort")]))
    state = await pipeline.run()
    assert state.status is RunStatus.ABORTED
    assert state.tasks == {}


async def test_execution_failure_offers_retry_then_fails_safe(tmp_path) -> None:
    workers = FakeWorkers(fail={"code"})
    gate = ScriptedGate()
    pipeline, _ = make_pipeline(tmp_path, workers=workers, gate=gate)
    state = await pipeline.run()

    failure_gates = [r for r in gate.requests if r.gate == "execution_failures"]
    assert [r.actions[0] for r in failure_gates] == ["retry", "abort"]  # retry once, then abort by default
    assert workers.runs.count("code") == 2
    assert state.status is RunStatus.ABORTED


async def test_human_can_accept_partial_results(tmp_path) -> None:
    gate = ScriptedGate([GateDecision(gate="execution_failures", action="approve")])
    pipeline, events = make_pipeline(tmp_path, workers=FakeWorkers(fail={"tests"}), gate=gate)
    state = await pipeline.run()
    assert state.status is RunStatus.COMPLETED
    assert state.tasks["tests"].status is TaskStatus.FAILED
    assert any(e["event"] == "partial_results_accepted" for e in events)


async def test_aborted_run_can_be_resumed_without_redoing_work(tmp_path) -> None:
    gate = ScriptedGate([GateDecision(gate="execution_failures", action="abort")])
    pipeline, _ = make_pipeline(tmp_path, workers=FakeWorkers(fail={"code"}), gate=gate)
    first = await pipeline.run()
    assert first.status is RunStatus.ABORTED

    saved = CheckpointStore(tmp_path / "runs").load(first.run_id)
    workers = FakeWorkers()
    resumed, _ = make_pipeline(tmp_path, workers=workers, state=saved)
    state = await resumed.run()

    assert state.status is RunStatus.COMPLETED
    assert workers.runs == ["code"]  # design and tests already succeeded; analysis/planning not redone
    assert [d.gate for d in state.decisions] == ["spec", "plan", "execution_failures", "output"]


async def test_brownfield_imports_repo_copy_and_runs_impact_analysis(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "main.py").write_text("x = 1\n")
    (repo / ".env").write_text("SECRET=1\n")
    analyst = FakeImpactAnalyst()
    state = RunState(requirement="Add expiry", repo_path=str(repo))
    pipeline, _ = make_pipeline(tmp_path, state=state, impact_analyst=analyst)
    state = await pipeline.run()

    assert state.status is RunStatus.COMPLETED
    assert state.impact.impacted_modules[0].path == "app/main.py"
    assert analyst.seen_files == [["app/main.py"]]  # secrets are never imported
    assert state.file_owners["app/main.py"] == "repo"
    run_dir = tmp_path / "runs" / state.run_id
    diff = (run_dir / "changes.diff").read_text()
    assert "+++ b/design.txt" in diff and "app/main.py" not in diff
    assert (repo / "app" / "main.py").read_text() == "x = 1\n"  # original untouched
    assert "## 3. Impact analysis" in (run_dir / "SUMMARY.md").read_text()


async def test_every_run_writes_a_summary_even_when_aborted(tmp_path) -> None:
    pipeline, _ = make_pipeline(tmp_path, gate=ScriptedGate([GateDecision(gate="plan", action="abort")]))
    state = await pipeline.run()
    summary = (tmp_path / "runs" / state.run_id / "SUMMARY.md").read_text()
    assert "**aborted**" in summary and "## 4. Implementation plan" in summary


async def test_brownfield_without_impact_analyst_fails_cleanly(tmp_path) -> None:
    pipeline, _ = make_pipeline(tmp_path, state=RunState(requirement="r", repo_path="/repo"))
    state = await pipeline.run()
    assert state.status is RunStatus.FAILED
    assert state.error.startswith("impact:")
