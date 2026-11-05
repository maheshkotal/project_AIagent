"""Structured engineering summary (SUMMARY.md), rendered deterministically from the run state."""

from __future__ import annotations

from collections import defaultdict

from agentforge.models import DesignDoc, RiskReport
from agentforge.orchestrator.render import impact_markdown, validation_markdown
from agentforge.orchestrator.state import RunState, TaskStatus

SYSTEM_LIMITATIONS = [
    "Generated code runs in a subprocess with a scrubbed environment and timeouts, not in a container; "
    "container isolation is the next hardening step.",
    "Validation proves the code satisfies its generated tests and the generated contract; it cannot prove the "
    "tests are exhaustive. The final human gate remains essential.",
    "LLM output is non-deterministic; record/replay cassettes make reviewed runs reproducible.",
    "Brownfield context is bounded (~200k characters of source); larger repositories need retrieval.",
]


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) or "- (none)"


def _design(state: RunState) -> DesignDoc | None:
    for result in state.tasks.values():
        if result.status is TaskStatus.SUCCEEDED and "design" in result.output:
            return DesignDoc.model_validate(result.output["design"])
    return None


def _risk(state: RunState) -> RiskReport | None:
    for result in state.tasks.values():
        if result.status is TaskStatus.SUCCEEDED and "risk_report" in result.output:
            return RiskReport.model_validate(result.output["risk_report"])
    return None


def _artifact_group(path: str) -> str:
    if path.startswith("tests/"):
        return "Tests"
    if path.startswith("contract/"):
        return "API contract"
    if path.endswith(".md"):
        return "Documentation"
    return "Code"


def build_summary(state: RunState, files: list[str], diff_stats: dict[str, tuple[int, int]] | None = None) -> str:
    spec, plan = state.spec, state.plan
    out = [f"# Engineering summary: {spec.title if spec else 'run ' + state.run_id}"]
    out.append(
        f"| | |\n|---|---|\n| Run | `{state.run_id}` |\n| Status | **{state.status.value}**"
        + (f" ({state.error})" if state.error else "")
        + f" |\n| Scenario | {spec.scenario.value if spec else '-'} |\n| Requirement | {state.requirement} |"
    )

    if spec:
        out.append(
            "## 1. Requirement understanding\n\n"
            f"{spec.problem_statement}\n\n**Functional requirements**\n{_bullets(spec.functional_requirements)}\n\n"
            f"**Non-functional requirements**\n{_bullets(spec.non_functional_requirements)}\n\n"
            f"**Out of scope**\n{_bullets(spec.out_of_scope)}"
        )
        decisions = "\n".join(
            f"| {d.gate} | {d.action} | {d.decided_by} | {d.feedback or '-'} |" for d in state.decisions
        )
        out.append(
            "## 2. Assumptions and human decisions\n\n"
            f"{_bullets(spec.assumptions)}\n\n| Gate | Decision | By | Feedback |\n|---|---|---|---|\n{decisions}"
        )

    if state.impact:
        out.append("## 3. Impact analysis (existing codebase)\n\n" + impact_markdown(state.impact))

    if plan:
        rows = []
        for step, layer in enumerate(plan.execution_layers(), start=1):
            for task_id in layer:
                task, result = plan.task(task_id), state.tasks.get(task_id)
                status = f"{result.status.value} ({result.attempts} attempt(s))" if result else "not run"
                deps = ", ".join(task.depends_on) or "-"
                rows.append(f"| {step} | `{task.id}` {task.title} | {task.agent} | {deps} | {status} |")
        out.append(
            "## 4. Implementation plan and rationale\n\n"
            f"{plan.rationale}\n\n| Step | Task | Agent | Depends on | Result |\n|---|---|---|---|---|\n"
            + "\n".join(rows)
            + "\n\n*Tasks in the same step ran in parallel.*"
        )

    design = _design(state)
    if design:
        decisions = "\n".join(f"- **{d.decision}**: {d.rationale}" for d in design.key_decisions)
        out.append(
            f"## 5. Architecture\n\n{design.overview}\n\n```mermaid\n{design.architecture_diagram}\n```\n\n"
            f"**Key decisions**\n{decisions}\n\n**Scaling strategy**\n\n{design.scaling_strategy}\n\n"
            "Full design: `docs/architecture.md`; contract: `contract/openapi.yaml`."
        )

    groups: dict[str, list[str]] = defaultdict(list)
    for path in files:
        owner = state.file_owners.get(path, "-")
        change = ""
        if diff_stats is not None and path in diff_stats:
            added, removed = diff_stats[path]
            change = f" (+{added}/-{removed})"
        groups[_artifact_group(path)].append(f"`{path}`{change} - {owner}")
    artifacts = "\n\n".join(f"**{group}**\n{_bullets(items)}" for group, items in sorted(groups.items()))
    note = "\n\nThe full change set is in `changes.diff`." if diff_stats is not None else ""
    out.append(f"## 6. Generated artifacts\n\nEach file lists the task that last wrote it.\n\n{artifacts}{note}")

    out.append("## 7. Validation\n\n" + validation_markdown(state) + "\n\n" + _validation_history(state))

    risk = _risk(state)
    if risk:
        risks = "\n".join(
            f"| {r.risk} | {r.likelihood.value} | {r.impact.value} | {r.mitigation} |" for r in risk.risks
        )
        tradeoffs = "\n".join(
            f"- **{t.decision}**: pros: {'; '.join(t.pros)}. cons: {'; '.join(t.cons)}" for t in risk.tradeoffs
        )
        out.append(
            "## 8. Risks, trade-offs and validation strategy\n\n| Risk | Likelihood | Impact | Mitigation |\n"
            f"|---|---|---|---|\n{risks}\n\n**Trade-offs**\n{tradeoffs}\n\n"
            f"**Validation strategy**\n{_bullets(risk.validation_strategy)}\n\nDetails: `docs/RISKS.md`."
        )

    limitations = (risk.assumptions_and_limitations if risk else []) + SYSTEM_LIMITATIONS
    out.append("## 9. Assumptions and limitations\n\n" + _bullets(limitations))

    m = state.metrics
    out.append(
        "## 10. Execution metrics\n\n"
        f"- LLM calls: {m.get('llm_calls', 0)} (transient-error retries: {m.get('llm_retries', 0)})\n"
        f"- Tokens: {m.get('input_tokens', 0)} in / {m.get('output_tokens', 0)} out\n"
        f"- Task retries: {m.get('task_retries', 0)}; plan revisions: {state.plan_revisions}; "
        f"repair rounds: {len(state.repairs)}"
    )
    return "\n\n".join(out) + "\n"


def _validation_history(state: RunState) -> str:
    if len(state.validations) < 2:
        return ""
    rows = []
    for report in state.validations:
        tests = report.tests
        counts = f"{tests.passed} passed / {tests.failed + tests.errors} failing" if tests else "-"
        failed = ", ".join(c.name for c in report.failed_checks()) or "-"
        rows.append(f"| {report.round} | {'passed' if report.passed else 'failed'} | {counts} | {failed} |")
    return "**History**\n\n| Round | Result | Tests | Failed checks |\n|---|---|---|---|\n" + "\n".join(rows)
