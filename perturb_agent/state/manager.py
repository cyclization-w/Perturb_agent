from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from perturb_agent.schemas.base import model_to_dict, model_to_json
from perturb_agent.state.schemas import AnalysisState, DecisionRecord, NextAction


class StateManager:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def create(cls, run_dir: Path) -> "StateManager":
        manager = cls(run_dir / "analysis_state.json")
        manager.ensure_exists()
        return manager

    def ensure_exists(self) -> None:
        if not self.path.exists():
            self.save(AnalysisState())

    def load(self) -> AnalysisState:
        self.ensure_exists()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return AnalysisState(**data)

    def save(self, state: AnalysisState) -> None:
        self.path.write_text(model_to_json(state), encoding="utf-8")

    def query(self, section: str | None = None) -> dict[str, Any]:
        state_dict = model_to_dict(self.load())
        if section is None or section == "all":
            return state_dict
        if section not in state_dict:
            raise KeyError(f"Unknown analysis_state section: {section}")
        value = state_dict[section]
        if isinstance(value, dict):
            return value
        return {section: value}

    def append_decision(self, decision: DecisionRecord) -> DecisionRecord:
        state = self.load()
        state.decisions.append(decision)
        self.save(state)
        return decision

    def commit_decision(
        self,
        *,
        decision_id: str,
        pi_confirmation_ref: str | None = None,
        modifications_from_proposal: dict[str, Any] | None = None,
    ) -> DecisionRecord:
        state = self.load()
        target: DecisionRecord | None = None
        for decision in reversed(state.decisions):
            if decision.decision_id == decision_id:
                target = decision
                break
        if target is None:
            raise KeyError(f"No proposed decision found for decision_id={decision_id!r}")
        if target.status == "committed":
            return target

        target.status = "committed"
        target.pi_confirmation_ref = pi_confirmation_ref
        target.modifications_from_proposal = modifications_from_proposal
        target.committed_at = datetime.now().isoformat()
        self.save(state)
        return target

    def apply_anndata_inspection(self, summary: dict[str, Any]) -> AnalysisState:
        state = self.load()
        dataset = state.dataset
        experimental_design = state.experimental_design

        dataset.current_adata_path = summary.get("path") or dataset.current_adata_path
        dataset.dataset_id = summary.get("dataset_id") or dataset.dataset_id

        shape = summary.get("shape") or {}
        dataset.n_cells = shape.get("n_cells") or dataset.n_cells
        dataset.n_genes = shape.get("n_genes") or dataset.n_genes
        dataset.obs_columns = list(summary.get("obs_columns") or [])
        dataset.var_columns = list(summary.get("var_columns") or [])
        dataset.uns_keys = list(summary.get("uns_keys") or [])

        candidates = summary.get("candidate_columns") or {}
        experimental_design.guide_column = _first(candidates.get("guide")) or experimental_design.guide_column
        experimental_design.target_column = _first(candidates.get("target")) or experimental_design.target_column
        experimental_design.batch_column = _first(candidates.get("batch")) or experimental_design.batch_column
        experimental_design.condition_column = _first(candidates.get("condition")) or experimental_design.condition_column
        experimental_design.timepoint_column = _first(candidates.get("timepoint")) or experimental_design.timepoint_column

        control_candidates = summary.get("control_label_candidates") or []
        experimental_design.control_design.non_targeting_labels = _merge_unique(
            experimental_design.control_design.non_targeting_labels,
            [str(item.get("label")) for item in control_candidates if item.get("label")],
        )

        unresolved = []
        for field in summary.get("missing_design_fields") or []:
            unresolved.append(
                {
                    "field": str(field),
                    "source": "inspect_anndata",
                    "status": "unresolved",
                }
            )
        experimental_design.unresolved_questions = _merge_unresolved(
            experimental_design.unresolved_questions,
            unresolved,
        )

        state.artifacts.append(
            {
                "type": "anndata_inspection",
                "path": summary.get("path"),
                "summary": {
                    "shape": summary.get("shape"),
                    "dataset_likelihood": summary.get("dataset_likelihood"),
                    "embedding_status": summary.get("embedding_status"),
                    "candidate_columns": summary.get("candidate_columns"),
                },
                "created_at": datetime.now().isoformat(),
            }
        )

        state.next_actions = [
            action
            for action in state.next_actions
            if action.action != "ask_pi_about_anndata_design"
        ]
        for question in summary.get("recommended_pi_questions") or []:
            state.next_actions.append(
                NextAction(
                    action="ask_pi_about_anndata_design",
                    reason="AnnData inspection found missing or ambiguous experimental-design fields.",
                    question=str(question),
                    blocking=True,
                )
            )

        if state.pipeline.current_stage == "design_check":
            state.pipeline.stage_status["design_check"] = "partial"
            state.pipeline.next_recommended_stage = "load"

        self.save(state)
        return state

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(model_to_dict(self.load()))


def _first(values: Any) -> str | None:
    if not values:
        return None
    return str(values[0])


def _merge_unique(existing: list[str], new_values: list[str]) -> list[str]:
    merged = list(existing)
    for value in new_values:
        if value not in merged:
            merged.append(value)
    return merged


def _merge_unresolved(existing: list[dict[str, Any]], new_values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {(item.get("field"), item.get("source")) for item in existing}
    merged = list(existing)
    for item in new_values:
        key = (item.get("field"), item.get("source"))
        if key not in seen:
            merged.append(item)
            seen.add(key)
    return merged
