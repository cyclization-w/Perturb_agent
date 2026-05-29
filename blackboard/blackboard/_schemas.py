"""Internal Pydantic models for the blackboard event-sourcing graph."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _model_dump(obj) -> dict:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "dict"):
        return obj.dict()
    return obj


# ── Event log ───────────────────────────────────────────────────────────

class Event(BaseModel):
    event_id: str
    event_type: str
    run_id: str
    timestamp: datetime = Field(default_factory=_now)
    actor: str = "system"
    payload: dict[str, Any] = Field(default_factory=dict)


# ── Budget ──────────────────────────────────────────────────────────────

class Budget(BaseModel):
    max_attempts: int = 20
    max_branches: int = 3
    max_repairs: int = 3


# ── Core nodes ──────────────────────────────────────────────────────────

class Attempt(BaseModel):
    attempt_id: str
    branch_id: str = "main"
    title: str = ""
    objective: str = ""
    stage: str = ""
    status: str = "planned"
    notebook_cells: list[dict] = Field(default_factory=list)
    capability_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_artifacts: list[str] = Field(default_factory=list)
    required_validators: list[str] = Field(default_factory=list)
    parent_ids: list[str] = Field(default_factory=list)
    parent_intervention: str = ""
    repair_count: int = 0
    rationale: str = ""
    created_at: datetime = Field(default_factory=_now)


class Outcome(BaseModel):
    outcome_id: str
    attempt_id: str
    status: str = "success"  # success, error, suspicious, unproductive, stopped
    summary: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)


class Artifact(BaseModel):
    artifact_id: str
    attempt_id: str = ""
    path: str = ""
    kind: str = ""
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class Observation(BaseModel):
    """A typed scientific reading extracted from analysis output.

    This is the core data type. Every quantitative finding — effect size,
    coverage, concordance, module score — is an Observation. The graph
    uses Observations to detect conflicts, thin evidence, and parameter
    sensitivity across attempts and branches.
    """

    observation_id: str
    type: str = "custom"  # de_effect, coverage, concordance, module_score, ...
    target: str = ""  # gene, module, cluster — what was measured
    metric: str = ""  # logFC, p_value, count, score
    value: Any = None
    contrast: str = ""
    method: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    uncertainty: dict[str, Any] = Field(default_factory=dict)
    attempt_id: str = ""
    branch_id: str = ""
    artifact_id: str = ""
    created_at: datetime = Field(default_factory=_now)


class ReviewTrigger(BaseModel):
    trigger_id: str
    attempt_id: str = ""
    trigger_type: str = ""
    severity: str = "warning"
    summary: str = ""
    status: str = "open"


class Finding(BaseModel):
    finding_id: str
    attempt_id: str = ""
    finding_type: str = "continue_ok"
    severity: str = "info"
    suggested_action: str = "continue"
    summary: str = ""
    affected_ids: list[str] = Field(default_factory=list)


class Branch(BaseModel):
    branch_id: str
    title: str = ""
    parent_id: str = ""
    reason: str = "main"
    status: str = "active"


class Goal(BaseModel):
    goal_id: str
    text: str = ""
    status: str = "active"


class Conclusion(BaseModel):
    conclusion_id: str
    text: str = ""
    grade: str = "inconclusive"
    support_ids: list[str] = Field(default_factory=list)
    limitation_ids: list[str] = Field(default_factory=list)


# ── Proposal models (LLM output) ───────────────────────────────────────

class NotebookCell(BaseModel):
    role: str = "execute"
    title: str = ""
    source: str


class AttemptProposal(BaseModel):
    proposal_id: str = ""
    title: str = ""
    objective: str = ""
    stage: str = ""
    capability_ids: list[str] = Field(default_factory=list)
    notebook_cells: list[NotebookCell] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    required_validators: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class InterventionProposal(BaseModel):
    proposal_id: str = ""
    trigger_id: str = ""
    intervention_type: str = ""  # fix_code, change_params, try_tool, rerun, open_branch, stop_branch, ask_user
    target_ids: list[str] = Field(default_factory=list)
    notebook_cells: list[NotebookCell] = Field(default_factory=list)
    rationale: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    branch_reason: str = ""  # for open_branch: parameter_sensitivity, tool_alternative, biological_hypothesis, negative_pivot


class Intervention(BaseModel):
    intervention_id: str
    trigger_id: str = ""
    intervention_type: str = ""
    status: str = "proposed"  # proposed, applied, rejected
    summary: str = ""
    target_ids: list[str] = Field(default_factory=list)
    notebook_cells: list[dict] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    branch_reason: str = ""
    created_at: str = ""


class Interrupt(BaseModel):
    interrupt_id: str
    source: str = ""  # critic_review, budget_exhausted, user_requested
    trigger_id: str = ""
    question: str = ""
    options: list[str] = Field(default_factory=list)
    default_action: str = "ask_user"
    status: str = "open"  # open, resolved, dismissed


# ── Graph (derived from events) ─────────────────────────────────────────

class GraphNode(BaseModel):
    node_id: str
    node_type: str
    label: str
    summary: str = ""
    status: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source_id: str
    target_id: str
    edge_type: str


class AttemptGraph(BaseModel):
    run_id: str = ""
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


# ── Snapshot (full state from event replay) ─────────────────────────────

class Snapshot(BaseModel):
    run_id: str = ""
    phase: str = "initialized"
    workspace: str = ""
    goal: str = ""
    domain: str = ""
    attempts: list[Attempt] = Field(default_factory=list)
    outcomes: list[Outcome] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    triggers: list[ReviewTrigger] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    interventions: list[Intervention] = Field(default_factory=list)
    interrupts: list[Interrupt] = Field(default_factory=list)
    branches: list[Branch] = Field(default_factory=list)
    goals: list[Goal] = Field(default_factory=list)
    conclusions: list[Conclusion] = Field(default_factory=list)
    capabilities: list[dict] = Field(default_factory=list)
    protocol: str = ""
    budget: Budget = Field(default_factory=Budget)
    active_branch: str = "main"
    active_attempt: str = ""


# ── Context (compiled for LLM consumption) ──────────────────────────────

class MemoryEntry(BaseModel):
    subject: str = ""
    metric: str = ""
    current_value: Any = None
    prior_values: list[dict] = Field(default_factory=list)
    signal: str = "no_prior_data"  # conflict, warning, agreement, thin, new
    summary: str = ""


class CoverageEntry(BaseModel):
    subject: str = ""
    methods: int = 0
    branches: int = 0
    observations: int = 0
    contradictions: int = 0
    label: str = "no_coverage"


class IntentEntry(BaseModel):
    branch_id: str = ""
    intent: str = ""  # serve_goal, repair, validate, explore, pivot, unknown
    drift: str = "low"  # low, medium, high
    summary: str = ""


class Context(BaseModel):
    run_id: str = ""
    phase: str = ""
    goal: str = ""
    active_stage: str = ""
    attempts_done: int = 0
    budget_remaining: dict[str, int] = Field(default_factory=dict)
    open_triggers: list[dict] = Field(default_factory=list)
    capabilities: list[dict] = Field(default_factory=list)
    memory: list[MemoryEntry] = Field(default_factory=list)
    coverage: list[CoverageEntry] = Field(default_factory=list)
    intent: list[IntentEntry] = Field(default_factory=list)
    recent_findings: list[dict] = Field(default_factory=list)
    protocol: str = ""
    workspace_files: list[dict] = Field(default_factory=list)
    truncated: bool = False
