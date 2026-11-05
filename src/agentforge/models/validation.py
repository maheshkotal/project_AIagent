"""Results of validating the generated workspace."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from agentforge.models.artifacts import ReviewVerdict


class CheckStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


class CheckResult(BaseModel):
    name: str
    status: CheckStatus
    summary: str
    details: list[str] = Field(default_factory=list)


class TestFailure(BaseModel):
    test_id: str
    message: str


class TestRun(BaseModel):
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failures: list[TestFailure] = Field(default_factory=list)
    output_tail: str = ""

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped


class ValidationReport(BaseModel):
    round: int
    checks: list[CheckResult]
    tests: TestRun | None = None

    @property
    def passed(self) -> bool:
        return all(c.status is not CheckStatus.FAILED for c in self.checks)

    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status is CheckStatus.FAILED]


class RepairRound(BaseModel):
    round: int
    verdict: ReviewVerdict
    files_changed: list[str]
    human_feedback: str = ""
