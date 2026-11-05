"""Normalized requirement produced by the Requirement Analyst."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ScenarioType(StrEnum):
    GREENFIELD = "greenfield"
    BROWNFIELD = "brownfield"
    TESTS_DOCS = "tests_docs"


class Impact(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Ambiguity(BaseModel):
    id: str = Field(description="Short stable identifier, e.g. 'A1'.")
    question: str = Field(description="The clarifying question a human should answer.")
    options: list[str] = Field(default_factory=list, description="Plausible answers, most sensible first.")
    default_assumption: str = Field(description="What will be assumed if the human does not answer.")
    impact: Impact = Field(description="How much the answer changes the design.")


class NormalizedSpec(BaseModel):
    title: str
    scenario: ScenarioType
    problem_statement: str = Field(description="One-paragraph restatement of the engineering problem.")
    functional_requirements: list[str]
    non_functional_requirements: list[str]
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)

    def resolve(self, answers: dict[str, str]) -> NormalizedSpec:
        """Turn every ambiguity into an explicit assumption, using the human's answer or the default."""
        resolved = [
            f"{a.question} -> {answers.get(a.id) or a.default_assumption}"
            + ("" if answers.get(a.id) else " (default assumption)")
            for a in self.ambiguities
        ]
        return self.model_copy(update={"ambiguities": [], "assumptions": [*self.assumptions, *resolved]})
