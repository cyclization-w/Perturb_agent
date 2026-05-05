from __future__ import annotations

from perturb_agent.state.schemas import DecisionRecord


class TierValidationError(ValueError):
    pass


def validate_proposal(decision: DecisionRecord) -> None:
    if not decision.tier:
        raise TierValidationError("propose_decision requires tier A, B, or C.")
    if not decision.tier_reason.strip():
        raise TierValidationError("propose_decision requires tier_reason.")
    if not decision.expected_outcome.strip():
        raise TierValidationError("propose_decision requires expected_outcome.")
    if decision.tier == "C" and not decision.requires_pi:
        raise TierValidationError("Tier C decisions must set requires_pi=true.")
    if decision.reversibility == "expensive" and not decision.requires_pi:
        raise TierValidationError("Expensive decisions require PI confirmation.")


def validate_commit_allowed(decision: DecisionRecord) -> None:
    if decision.tier == "C" and not decision.pi_confirmation_ref:
        raise TierValidationError("Tier C decisions cannot be committed without pi_confirmation_ref.")
    if decision.reversibility == "expensive" and not decision.pi_confirmation_ref:
        raise TierValidationError(
            "Expensive decisions cannot be committed without pi_confirmation_ref."
        )
