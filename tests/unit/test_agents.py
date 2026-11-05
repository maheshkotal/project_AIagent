from __future__ import annotations

import pytest

from agentforge.agents.context import FileConflictError, task_write_policy, write_files
from agentforge.agents.planning import plan_rules
from agentforge.models import GeneratedFile, NormalizedSpec, ScenarioType, Task, TaskGraph
from agentforge.orchestrator.dag import TaskContext
from agentforge.orchestrator.state import RunState
from agentforge.tools import checks
from agentforge.tools.sandbox import Sandbox

PLAN = TaskGraph(
    rationale="r",
    tasks=[
        Task(id="design", title="d", agent="architect", description="d"),
        Task(id="storage", title="s", agent="coder", description="d", depends_on=["design"]),
        Task(id="api", title="a", agent="coder", description="d", depends_on=["storage"]),
        Task(id="analytics", title="a", agent="coder", description="d", depends_on=["design"]),
        Task(id="tests", title="t", agent="tester", description="d", depends_on=["design"]),
    ],
)
SPEC = NormalizedSpec(
    title="t",
    scenario=ScenarioType.GREENFIELD,
    problem_statement="p",
    functional_requirements=[],
    non_functional_requirements=[],
)


def ctx_for(task_id: str, state: RunState, ws: Sandbox) -> TaskContext:
    return TaskContext(
        task=PLAN.task(task_id), attempt=1, dependency_outputs={}, previous_error=None, state=state, workspace=ws
    )


def write(state, ws, role, owner, path, policy=lambda _o: True):
    return write_files(
        state, ws, role=role, owner=owner, files=[GeneratedFile(path=path, content="x")], may_overwrite=policy
    )


def test_roles_can_only_write_their_paths(tmp_path) -> None:
    state, ws = RunState(requirement="r", plan=PLAN), Sandbox(tmp_path)
    assert write(state, ws, "coder", "storage", "./app/db.py") == ["app/db.py"]
    assert state.file_owners["app/db.py"] == "storage"
    for role, path in [
        ("coder", "tests/test_x.py"),
        ("tester", "app/main.py"),
        ("docs", "app/x.py"),
        ("coder", "contract/openapi.yaml"),
    ]:
        with pytest.raises(FileConflictError, match="outside the paths"):
            write(state, ws, role, "t", path)


def test_parallel_tasks_cannot_overwrite_each_other_but_descendants_can(tmp_path) -> None:
    state, ws = RunState(requirement="r", plan=PLAN), Sandbox(tmp_path)
    write(state, ws, "coder", "storage", "app/db.py")
    # 'analytics' runs in parallel with 'storage': conflict.
    with pytest.raises(FileConflictError, match="owned by task 'storage'"):
        write(state, ws, "coder", "analytics", "app/db.py", task_write_policy(ctx_for("analytics", state, ws)))
    # 'api' depends on 'storage': allowed to extend its files.
    write(state, ws, "coder", "api", "app/db.py", task_write_policy(ctx_for("api", state, ws)))
    assert state.file_owners["app/db.py"] == "api"


def test_conflicting_batch_writes_nothing(tmp_path) -> None:
    state, ws = RunState(requirement="r", plan=PLAN), Sandbox(tmp_path)
    with pytest.raises(FileConflictError):
        write_files(
            state,
            ws,
            role="coder",
            owner="api",
            may_overwrite=lambda _o: True,
            files=[GeneratedFile(path="app/ok.py", content="x"), GeneratedFile(path="tests/no.py", content="x")],
        )
    assert ws.list_files() == []


def test_plan_rules_accept_contract_first_plan() -> None:
    assert plan_rules(PLAN, SPEC) == []


def test_plan_rules_reject_missing_architect_dependency_and_roles() -> None:
    bad = TaskGraph(
        rationale="r",
        tasks=[
            Task(id="design", title="d", agent="architect", description="d"),
            Task(id="code", title="c", agent="coder", description="d"),
        ],
    )
    errors = " | ".join(plan_rules(bad, SPEC))
    assert "'code' must depend" in errors
    assert "at least one 'tester'" in errors


def test_import_guardrail_flags_non_allowlisted_packages(tmp_path) -> None:
    ws = Sandbox(tmp_path)
    ws.write_text("app/__init__.py", "")
    ws.write_text("app/main.py", "import os\nimport fastapi\nfrom app import x\nimport requests\nfrom . import y\n")
    result = checks.check_imports(ws)
    assert result.status.value == "failed"
    assert result.details == ["app/main.py:4: import of non-allow-listed package 'requests'"]


def test_syntax_check_reports_file_and_line(tmp_path) -> None:
    ws = Sandbox(tmp_path)
    ws.write_text("app/main.py", "def broken(:\n")
    result = checks.check_syntax(ws)
    assert result.status.value == "failed"
    assert result.details[0].startswith("app/main.py:1")


async def test_tests_check_fails_when_no_tests_exist(tmp_path) -> None:
    ws = Sandbox(tmp_path)
    ws.write_text("app/main.py", "x = 1\n")
    result, run = await checks.run_tests(ws)
    assert result.status.value == "failed" and run is None
