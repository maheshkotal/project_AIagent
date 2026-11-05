"""Prompt-context rendering and workspace file I/O shared by the worker agents.

Prompts must be deterministic (stable ordering, no timestamps/run ids/absolute paths) so that
record/replay cassettes match across runs.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import PurePosixPath

from agentforge.models import (
    DesignDoc,
    GeneratedFile,
    NormalizedSpec,
    Task,
    ValidationReport,
)
from agentforge.orchestrator.dag import TaskContext
from agentforge.orchestrator.state import RunState, TaskStatus
from agentforge.tools.checks import CONTRACT_PATH
from agentforge.tools.sandbox import Sandbox

MAX_FILE_CHARS = 24_000
REPO_OWNER = "repo"


class FileConflictError(Exception):
    """An agent tried to write outside its role's paths or over another task's files (retryable)."""


def is_test_path(path: str) -> bool:
    return path.startswith("tests/") or PurePosixPath(path).name == "conftest.py"


def is_source_path(path: str) -> bool:
    return path.endswith(".py") and not is_test_path(path)


ROLE_WRITE_RULES: dict[str, Callable[[str], bool]] = {
    "architect": lambda p: p.startswith(("contract/", "docs/")),
    "coder": lambda p: not is_test_path(p) and not p.startswith(("contract/", "docs/")) and p != "README.md",
    "tester": is_test_path,
    "docs": lambda p: p == "README.md" or p.startswith("docs/"),
    "risk": lambda p: p.startswith("docs/"),
}


def normalize_path(path: str) -> str:
    return PurePosixPath(path.strip().removeprefix("./")).as_posix()


def write_files(
    state: RunState,
    workspace: Sandbox,
    *,
    role: str,
    owner: str,
    files: Iterable[GeneratedFile],
    may_overwrite: Callable[[str | None], bool],
) -> list[str]:
    """Write agent output after checking role path rules and file ownership for every file first."""
    staged = [(normalize_path(f.path), f.content) for f in files]
    problems = [
        f"'{p}' is outside the paths the {role} role may write" for p, _ in staged if not ROLE_WRITE_RULES[role](p)
    ]
    problems += [
        f"'{p}' is owned by task '{state.file_owners[p]}'"
        for p, _ in staged
        if not may_overwrite(state.file_owners.get(p))
    ]
    if problems:
        raise FileConflictError("Refused to write files: " + "; ".join(problems))
    for path, content in staged:
        workspace.write_text(path, content)
        state.file_owners[path] = owner
    return [p for p, _ in staged]


def task_write_policy(ctx: TaskContext) -> Callable[[str | None], bool]:
    """During plan execution a task may overwrite only its own, its ancestors' or pre-existing repo files."""
    assert ctx.state.plan is not None
    allowed = {ctx.task.id, REPO_OWNER, *ctx.state.plan.ancestors(ctx.task.id)}
    return lambda owner: owner is None or owner in allowed


# ---- rendering ----------------------------------------------------------------------------------


def section(title: str, body: str) -> str:
    return f"## {title}\n\n{body.strip()}\n"


def bullets(items: Iterable[str]) -> str:
    return "\n".join(f"- {item}" for item in items) or "- (none)"


def render_spec(spec: NormalizedSpec) -> str:
    return "\n\n".join(
        [
            f"**{spec.title}** ({spec.scenario.value})",
            spec.problem_statement,
            f"Functional requirements:\n{bullets(spec.functional_requirements)}",
            f"Non-functional requirements:\n{bullets(spec.non_functional_requirements)}",
            f"Assumptions and decisions:\n{bullets(spec.assumptions)}",
            f"Out of scope:\n{bullets(spec.out_of_scope)}",
        ]
    )


def render_task(task: Task) -> str:
    return "\n\n".join(
        [
            f"**{task.id}**: {task.title}",
            task.description,
            f"Acceptance criteria:\n{bullets(task.acceptance_criteria)}",
        ]
    )


def render_other_tasks(state: RunState, current: Task) -> str:
    assert state.plan is not None
    return bullets(f"`{t.id}` ({t.agent}): {t.description}" for t in state.plan.tasks if t.id != current.id)


def render_design(design: DesignDoc, *, include_contract: bool = True) -> str:
    modules = "\n\n".join(
        f"### `{m.path}`\n{m.responsibility}\n\n" + "\n".join(f"    {sig}" for sig in m.public_interface)
        for m in design.modules
    )
    parts = [
        section("Architecture overview", design.overview),
        section("Modules and public interfaces (binding)", modules),
        section("Data model", design.data_model),
    ]
    if include_contract:
        parts.append(section(f"API contract ({CONTRACT_PATH}, binding)", f"```yaml\n{design.openapi_yaml}\n```"))
    return "\n".join(parts)


def render_files(workspace: Sandbox, paths: Iterable[str]) -> str:
    blocks = []
    for path in sorted(paths):
        content = workspace.read_text(path)
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + "\n# [truncated]"
        blocks.append(f"### `{path}`\n```\n{content}\n```")
    return "\n\n".join(blocks) or "(none)"


def render_validation(report: ValidationReport) -> str:
    lines = [f"- **{c.name}**: {c.status.value} - {c.summary}" for c in report.checks]
    for check in report.failed_checks():
        if check.details:
            lines.append(f"\n### {check.name} details\n" + "\n".join(f"- {d}" for d in check.details[:25]))
    if report.tests and (report.tests.failed or report.tests.errors):
        lines.append(f"\n### pytest output (tail)\n```\n{report.tests.output_tail}\n```")
    return "\n".join(lines)


def retry_note(ctx: TaskContext) -> str:
    if not ctx.previous_error:
        return ""
    return section("Your previous attempt failed", f"{ctx.previous_error}\n\nCorrect this in your new response.")


def find_design(state: RunState, among: Iterable[str] | None = None) -> DesignDoc:
    """The architect's design, taken from the given tasks (e.g. ancestors) or any succeeded architect task."""
    candidates = list(among) if among is not None else list(state.tasks)
    for task_id in sorted(candidates):
        result = state.tasks.get(task_id)
        if result and result.status is TaskStatus.SUCCEEDED and "design" in result.output:
            return DesignDoc.model_validate(result.output["design"])
    raise LookupError("No completed architect design is available to this task")


def files_owned_by(state: RunState, owners: set[str], predicate: Callable[[str], bool]) -> list[str]:
    return sorted(p for p, o in state.file_owners.items() if o in owners and predicate(p))
