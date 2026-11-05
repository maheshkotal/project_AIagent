"""Review-and-repair: diagnose validation failures, then route fixes to the right agent(s)."""

from __future__ import annotations

import asyncio

from agentforge.agents.base import LLMAgent
from agentforge.agents.context import (
    files_owned_by,
    find_design,
    is_source_path,
    is_test_path,
    render_design,
    render_files,
    render_spec,
    render_validation,
    section,
)
from agentforge.agents.workers import CoderAgent, TesterAgent
from agentforge.audit import AuditLog
from agentforge.models import RepairRound, ReviewVerdict, ValidationReport
from agentforge.orchestrator.state import RunState
from agentforge.tools.sandbox import Sandbox


class ReviewerAgent(LLMAgent):
    name = "reviewer"
    prompt_files = ("conventions", "reviewer")

    async def diagnose(
        self, state: RunState, workspace: Sandbox, report: ValidationReport, feedback: str | None
    ) -> ReviewVerdict:
        assert state.spec is not None
        owners = set(state.file_owners.values())
        parts = [
            section("Requirement", render_spec(state.spec)),
            render_design(find_design(state)),
            section("Validation results", render_validation(report)),
        ]
        if feedback:
            parts.append(section("Human reviewer feedback (must be addressed)", feedback))
        parts += [
            section("Application files", render_files(workspace, files_owned_by(state, owners, is_source_path))),
            section("Test files", render_files(workspace, files_owned_by(state, owners, is_test_path))),
        ]
        return await self.ask("\n".join(parts), ReviewVerdict)


class RepairCoordinator:
    """One repair round: the reviewer attributes fault, then code and/or test fixes run in parallel."""

    def __init__(self, reviewer: ReviewerAgent, coder: CoderAgent, tester: TesterAgent, audit: AuditLog) -> None:
        self._reviewer = reviewer
        self._coder = coder
        self._tester = tester
        self._audit = audit

    async def repair(
        self, state: RunState, workspace: Sandbox, report: ValidationReport, feedback: str | None = None
    ) -> RepairRound:
        round_no = len(state.repairs) + 1
        verdict = await self._reviewer.diagnose(state, workspace, report, feedback)
        self._audit.record("review_verdict", round=round_no, fault=verdict.fault, rationale=verdict.rationale)
        owner = f"repair-{round_no}"
        jobs = []
        if verdict.fault in ("code", "both"):
            jobs.append(
                self._coder.repair(state, workspace, report, verdict.code_fix_instructions or verdict.rationale, owner)
            )
        if verdict.fault in ("tests", "both"):
            jobs.append(
                self._tester.repair(state, workspace, report, verdict.test_fix_instructions or verdict.rationale, owner)
            )
        changed = sorted(path for paths in await asyncio.gather(*jobs) for path in paths)
        self._audit.record("repair_applied", round=round_no, files=changed)
        return RepairRound(round=round_no, verdict=verdict, files_changed=changed, human_feedback=feedback or "")
