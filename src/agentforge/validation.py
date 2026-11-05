"""Deterministic validation of a generated workspace (no LLM involved)."""

from __future__ import annotations

import asyncio

from agentforge.models import ValidationReport
from agentforge.orchestrator.state import RunState
from agentforge.tools import checks
from agentforge.tools.sandbox import Sandbox


class WorkspaceValidator:
    def __init__(self, *, test_timeout_s: float = 180) -> None:
        self._test_timeout_s = test_timeout_s

    async def validate(self, state: RunState, workspace: Sandbox, round: int) -> ValidationReport:
        static = [
            checks.check_syntax(workspace),
            checks.check_imports(workspace),
            checks.check_contract_document(workspace),
        ]
        # Independent dynamic checks run concurrently.
        lint, conformance, (tests_check, test_run) = await asyncio.gather(
            checks.check_lint(workspace),
            checks.check_contract_conformance(workspace),
            checks.run_tests(workspace, self._test_timeout_s),
        )
        return ValidationReport(round=round, checks=[*static, lint, conformance, tests_check], tests=test_run)
