from __future__ import annotations

from agentforge.models import Ambiguity, Impact, NormalizedSpec, ScenarioType, Task, TaskGraph


def graph(*edges: tuple[str, list[str]], agent: str = "coder") -> TaskGraph:
    return TaskGraph(
        rationale="r",
        tasks=[Task(id=tid, title=tid, agent=agent, description="d", depends_on=deps) for tid, deps in edges],
    )


def test_execution_layers_group_independent_tasks() -> None:
    g = graph(("design", []), ("code", ["design"]), ("tests", ["design"]), ("validate", ["code", "tests"]))
    assert g.execution_layers() == [["design"], ["code", "tests"], ["validate"]]
    assert g.validation_errors({"coder"}) == []


def test_detects_cycles() -> None:
    g = graph(("a", ["c"]), ("b", ["a"]), ("c", ["b"]))
    assert any("cycle" in e for e in g.validation_errors())


def test_detects_structural_errors() -> None:
    g = graph(("a", ["a"]), ("a", []), ("b", ["missing"]))
    errors = " | ".join(g.validation_errors({"architect"}))
    assert "Duplicate task ids: a" in errors
    assert "depends on itself" in errors
    assert "unknown task 'missing'" in errors
    assert "unknown agent 'coder'" in errors


def test_empty_plan_is_invalid() -> None:
    assert TaskGraph(rationale="r", tasks=[]).validation_errors() == ["The plan contains no tasks."]


def test_spec_resolve_turns_ambiguities_into_assumptions() -> None:
    spec = NormalizedSpec(
        title="t",
        scenario=ScenarioType.GREENFIELD,
        problem_statement="p",
        functional_requirements=[],
        non_functional_requirements=[],
        ambiguities=[
            Ambiguity(id="A1", question="Auth?", default_assumption="API keys", impact=Impact.HIGH),
            Ambiguity(id="A2", question="Expiry?", default_assumption="No expiry", impact=Impact.LOW),
        ],
        assumptions=["SQLite"],
    )
    resolved = spec.resolve({"A1": "OAuth"})
    assert resolved.ambiguities == []
    assert resolved.assumptions == ["SQLite", "Auth? -> OAuth", "Expiry? -> No expiry (default assumption)"]
