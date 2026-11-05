"""Markdown views of run artifacts, shown to humans at approval gates."""

from __future__ import annotations

from agentforge.models import ImpactReport, NormalizedSpec, TaskGraph
from agentforge.orchestrator.state import RunState


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- (none)"


def spec_markdown(spec: NormalizedSpec) -> str:
    return "\n\n".join(
        [
            f"## {spec.title}  \n*Scenario: {spec.scenario.value}*",
            spec.problem_statement,
            f"**Functional requirements**\n{_bullets(spec.functional_requirements)}",
            f"**Non-functional requirements**\n{_bullets(spec.non_functional_requirements)}",
            f"**Assumptions**\n{_bullets(spec.assumptions)}",
            f"**Out of scope**\n{_bullets(spec.out_of_scope)}",
            f"**Open questions:** {len(spec.ambiguities)} (answer below, or press Enter to accept the default)"
            if spec.ambiguities
            else "**Open questions:** none",
        ]
    )


def impact_markdown(impact: ImpactReport) -> str:
    items = [
        f"`{i.path}` ({i.change_type.value}): {i.reason}" for i in [*impact.impacted_modules, *impact.impacted_apis]
    ]
    return "\n\n".join(
        [
            f"**Existing architecture**\n{impact.architecture_summary}",
            f"**Impacted modules / APIs**\n{_bullets(items)}",
            f"**Data flows**\n{_bullets(impact.data_flows)}",
        ]
    )


def plan_markdown(plan: TaskGraph, impact: ImpactReport | None = None) -> str:
    rows = ["| Step | Task | Agent | Depends on |", "|---|---|---|---|"]
    for step, layer in enumerate(plan.execution_layers(), start=1):
        for task_id in layer:
            task = plan.task(task_id)
            rows.append(
                f"| {step} | **{task.id}**: {task.title} | {task.agent} | {', '.join(task.depends_on) or '-'} |"
            )
    parts = [f"**Rationale:** {plan.rationale}", "\n".join(rows), "*Tasks in the same step run in parallel.*"]
    if impact is not None:
        parts.insert(0, impact_markdown(impact))
    return "\n\n".join(parts)


def tasks_markdown(state: RunState) -> str:
    rows = ["| Task | Status | Attempts | Notes |", "|---|---|---|---|"]
    for result in state.tasks.values():
        note = (result.error or "").replace("\n", " ")[:120]
        rows.append(f"| {result.task_id} | {result.status.value} | {result.attempts} | {note} |")
    return "\n".join(rows)


def validation_markdown(state: RunState) -> str:
    report = state.latest_validation
    if report is None:
        return "*No validation was run.*"
    rows = ["| Check | Status | Summary |", "|---|---|---|"]
    rows += [f"| {c.name} | {c.status.value} | {c.summary} |" for c in report.checks]
    verdict = "PASSED" if report.passed else "FAILED"
    lines = [f"**Validation round {report.round}: {verdict}**", "\n".join(rows)]
    for check in report.failed_checks():
        lines.append(f"**{check.name}**\n" + _bullets([d.replace("\n", " ")[:200] for d in check.details[:10]]))
    if state.repairs:
        lines.append(
            "**Repair rounds**\n"
            + _bullets(
                [
                    f"Round {r.round}: fault={r.verdict.fault}; changed {', '.join(r.files_changed) or 'nothing'}"
                    for r in state.repairs
                ]
            )
        )
    return "\n\n".join(lines)


def output_markdown(state: RunState, files: list[str]) -> str:
    return "\n\n".join(
        [
            tasks_markdown(state),
            validation_markdown(state),
            "**Workspace files**\n" + "\n".join(f"- `{f}`" for f in files),
        ]
    )
