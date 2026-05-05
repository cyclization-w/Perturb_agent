from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


StageName = Literal[
    "design_check",
    "load",
    "qc",
    "guide_assignment",
    "efficacy_check",
    "target_qc",
    "state_reference",
    "perturb_effect",
    "target_analysis",
    "report",
]

StageStatus = Literal["not_started", "running", "partial", "complete", "blocked", "needs_review"]
DecisionTier = Literal["A", "B", "C"]
Reversibility = Literal["trivial", "moderate", "expensive"]


class DatasetState(BaseModel):
    project_name: str | None = None
    dataset_id: str | None = None
    organism: str | None = None
    tissue_or_cell_line: str | None = None
    platform: str = "unknown"
    current_adata_path: str | None = None
    n_cells: int | None = None
    n_genes: int | None = None
    obs_columns: list[str] = Field(default_factory=list)
    var_columns: list[str] = Field(default_factory=list)
    uns_keys: list[str] = Field(default_factory=list)
    raw_files: list[dict[str, Any]] = Field(default_factory=list)


class ControlDesignState(BaseModel):
    non_targeting_labels: list[str] = Field(default_factory=list)
    positive_control_targets: list[str] = Field(default_factory=list)


class ExperimentalDesignState(BaseModel):
    perturbation_type: str = "unknown"
    guide_capture: str = "unknown"
    loading_strategy: str = "unknown"
    moi_design: str = "unknown"
    guide_column: str | None = None
    target_column: str | None = None
    batch_column: str | None = None
    replicate_column: str | None = None
    timepoint_column: str | None = None
    condition_column: str | None = None
    control_design: ControlDesignState = Field(default_factory=ControlDesignState)
    known_special_designs: list[dict[str, str]] = Field(default_factory=list)
    unresolved_questions: list[dict[str, Any]] = Field(default_factory=list)


class PipelineState(BaseModel):
    current_stage: StageName = "design_check"
    completed_stages: list[StageName] = Field(default_factory=list)
    next_recommended_stage: StageName | None = None
    stage_status: dict[str, StageStatus] = Field(
        default_factory=lambda: {
            "design_check": "not_started",
            "load": "not_started",
            "qc": "not_started",
            "guide_assignment": "not_started",
            "efficacy_check": "not_started",
            "target_qc": "not_started",
            "state_reference": "not_started",
            "perturb_effect": "not_started",
            "target_analysis": "not_started",
            "report": "not_started",
        }
    )


class DecisionRecord(BaseModel):
    decision_id: str
    stage: str
    tier: DecisionTier
    tier_reason: str
    requires_pi: bool
    reversibility: Reversibility
    decision: dict[str, Any]
    reasoning: str
    expected_outcome: str
    evidence_refs: list[str] = Field(default_factory=list)
    alternatives_considered: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["proposed", "committed", "rejected"] = "proposed"
    pi_confirmation_ref: str | None = None
    modifications_from_proposal: dict[str, Any] | None = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    committed_at: str | None = None


class IssueRecord(BaseModel):
    issue_id: str
    stage: str | None = None
    severity: Literal["info", "warning", "critical"] = "info"
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    blocking: bool = False
    status: Literal["open", "resolved"] = "open"


class AssumptionRecord(BaseModel):
    assumption_id: str
    text: str
    confidence: Literal["low", "medium", "high"] = "low"
    needs_pi_confirmation: bool = True


class PiPreferenceRecord(BaseModel):
    preference: str
    scope: Literal["this_project", "lab_wide"] = "this_project"
    source_quote: str


class NextAction(BaseModel):
    action: str
    reason: str
    question: str | None = None
    blocking: bool = False


class AnalysisState(BaseModel):
    dataset: DatasetState = Field(default_factory=DatasetState)
    experimental_design: ExperimentalDesignState = Field(default_factory=ExperimentalDesignState)
    pipeline: PipelineState = Field(default_factory=PipelineState)
    decisions: list[DecisionRecord] = Field(default_factory=list)
    open_issues: list[IssueRecord] = Field(default_factory=list)
    assumptions: list[AssumptionRecord] = Field(default_factory=list)
    pi_preferences: list[PiPreferenceRecord] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
