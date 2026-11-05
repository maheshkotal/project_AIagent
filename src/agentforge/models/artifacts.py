"""Structured outputs of the worker agents (design, generated files, risk analysis, review verdicts)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from agentforge.models.spec import Impact
from agentforge.tools.openapi import contract_problems


class GeneratedFile(BaseModel):
    path: str = Field(description="Workspace-relative POSIX path, e.g. 'app/main.py'.")
    content: str = Field(description="The complete file content. Never partial, never placeholders.")


class FileBundle(BaseModel):
    summary: str = Field(description="What these files implement and any notable decisions.")
    files: list[GeneratedFile]


class ModuleSpec(BaseModel):
    path: str = Field(description="File path, e.g. 'app/services/shortener.py'.")
    responsibility: str
    public_interface: list[str] = Field(
        description="Exact Python signatures other modules and tests rely on, e.g. "
        "'def encode_base62(n: int) -> str' or 'class LinkRepository: def get(self, code: str) -> Link | None'."
    )


class DesignDecision(BaseModel):
    decision: str
    rationale: str
    alternatives_considered: list[str] = Field(default_factory=list)


class DesignDoc(BaseModel):
    overview: str
    architecture_diagram: str = Field(description="Mermaid flowchart source (without code fences).")
    modules: list[ModuleSpec]
    data_model: str = Field(description="Tables/entities with columns, types, keys and indexes (SQL DDL).")
    openapi_yaml: str = Field(description="Complete OpenAPI 3.1 contract in YAML.")
    key_decisions: list[DesignDecision]
    scaling_strategy: str = Field(description="How the design scales beyond the prototype (caching, sharding, etc.).")

    @field_validator("openapi_yaml")
    @classmethod
    def _contract_must_be_valid(cls, value: str) -> str:
        # Raising here feeds the problems back to the model via the structured-output repair loop.
        problems = contract_problems(value)
        if problems:
            raise ValueError("; ".join(problems))
        return value


class RiskItem(BaseModel):
    risk: str
    likelihood: Impact
    impact: Impact
    mitigation: str


class TradeOff(BaseModel):
    decision: str
    pros: list[str]
    cons: list[str]


class FailureScenario(BaseModel):
    scenario: str
    detection: str
    handling: str


class RiskReport(BaseModel):
    risks: list[RiskItem]
    tradeoffs: list[TradeOff]
    failure_scenarios: list[FailureScenario]
    validation_strategy: list[str] = Field(description="Test strategy and verification steps.")
    assumptions_and_limitations: list[str]


class ReviewVerdict(BaseModel):
    fault: Literal["code", "tests", "both"] = Field(
        description="Where the defect is: the application code, the tests, or both."
    )
    rationale: str = Field(description="Root-cause analysis referencing the specific failures.")
    code_fix_instructions: str = Field(default="", description="Concrete instructions for the code generator.")
    test_fix_instructions: str = Field(default="", description="Concrete instructions for the test generator.")
