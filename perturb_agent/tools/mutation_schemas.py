from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class QueryStateInput(BaseModel):
    section: str | None = Field(
        default=None,
        description="Optional analysis_state section, e.g. dataset, experimental_design, pipeline.",
    )


class InspectAnnDataInput(BaseModel):
    path: str
    dataset_id: str | None = None


class ProposeDecisionInput(BaseModel):
    decision_id: str
    stage: str
    tier: Literal["A", "B", "C"]
    tier_reason: str
    requires_pi: bool
    reversibility: Literal["trivial", "moderate", "expensive"]
    decision: dict[str, Any]
    reasoning: str
    expected_outcome: str
    evidence_refs: list[str] = Field(default_factory=list)
    alternatives_considered: list[dict[str, Any]] = Field(default_factory=list)


class CommitDecisionInput(BaseModel):
    decision_id: str
    pi_confirmation_ref: str | None = None
    modifications_from_proposal: dict[str, Any] | None = None


class ExecutePythonInput(BaseModel):
    code: str
    title: str = "Run Python code"


class RequestPiInput(BaseModel):
    question: str
    context_summary: str
    options: list[dict[str, str]]
    your_recommendation: str
    urgency: Literal["blocking", "soon", "whenever"]


class GraphFinishInput(BaseModel):
    summary: str
