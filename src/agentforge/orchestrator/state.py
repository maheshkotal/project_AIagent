"""Serializable run state: the shared blackboard every phase reads from and writes to."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentforge.models import ImpactReport, NormalizedSpec, RepairRound, TaskGraph, ValidationReport


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_run_id() -> str:
    return f"{utcnow():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskResult(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


GateAction = Literal["approve", "reject", "retry", "abort"]

# Owner recorded for files imported from an existing repository (brownfield).
REPO_OWNER = "repo"


class GateDecision(BaseModel):
    gate: str
    action: GateAction
    feedback: str = ""
    answers: dict[str, str] = Field(default_factory=dict)
    decided_by: Literal["human", "auto"] = "human"
    at: datetime = Field(default_factory=utcnow)


class RunState(BaseModel):
    run_id: str = Field(default_factory=new_run_id)
    requirement: str
    repo_path: str | None = None
    status: RunStatus = RunStatus.RUNNING
    created_at: datetime = Field(default_factory=utcnow)
    completed_phases: list[str] = Field(default_factory=list)
    spec: NormalizedSpec | None = None
    impact: ImpactReport | None = None
    plan: TaskGraph | None = None
    plan_revisions: int = 0
    tasks: dict[str, TaskResult] = Field(default_factory=dict)
    # Which task (or REPO_OWNER / "repair-N") last wrote each workspace file; used to detect write conflicts.
    file_owners: dict[str, str] = Field(default_factory=dict)
    validations: list[ValidationReport] = Field(default_factory=list)
    repairs: list[RepairRound] = Field(default_factory=list)
    decisions: list[GateDecision] = Field(default_factory=list)
    metrics: dict[str, int] = Field(default_factory=dict)
    error: str | None = None

    def outputs_of(self, task_ids: list[str]) -> dict[str, dict[str, Any]]:
        return {tid: self.tasks[tid].output for tid in task_ids if tid in self.tasks}

    @property
    def latest_validation(self) -> ValidationReport | None:
        return self.validations[-1] if self.validations else None
