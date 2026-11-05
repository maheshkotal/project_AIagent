"""Brownfield impact analysis produced by the Codebase Analyst."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ChangeType(StrEnum):
    ADD = "add"
    MODIFY = "modify"
    DELETE = "delete"
    READ_ONLY = "read_only"


class ImpactedItem(BaseModel):
    path: str = Field(description="Repository-relative file path, or an API route such as 'POST /links'.")
    change_type: ChangeType
    reason: str


class ImpactReport(BaseModel):
    architecture_summary: str = Field(description="How the existing system is structured, in a few sentences.")
    impacted_modules: list[ImpactedItem]
    impacted_apis: list[ImpactedItem] = Field(default_factory=list)
    data_flows: list[str] = Field(default_factory=list, description="Data flows touched by the change.")
    risks: list[str] = Field(default_factory=list)
