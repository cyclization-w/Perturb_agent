"""Context compilation + observation memory queries for LLM consumption."""

from __future__ import annotations

from blackboard._schemas import Snapshot, Context, MemoryEntry, CoverageEntry, IntentEntry


def compile_context(snap: Snapshot, *, max_items: int = 12) -> Context:
    memory = _build_memory(snap)
    coverage = _build_coverage(snap)
    intent = _build_intent(snap)

    # Workspace observations are never truncated — they're foundational context
    ws_items = [{"subject": o.target, "metric": o.metric, "value": o.value}
                for o in snap.observations if o.type == "workspace_file"]

    return Context(
        run_id=snap.run_id, phase=snap.phase, goal=snap.goal,
        protocol=snap.protocol,
        workspace_files=ws_items,
        active_stage=_active_stage(snap),
        attempts_done=len([a for a in snap.attempts if a.status != "planned"]),
        budget_remaining={
            "attempts": max(0, snap.budget.max_attempts - len(snap.attempts)),
            "branches": max(0, snap.budget.max_branches - len([b for b in snap.branches if b.status == "active"])),
        },
        open_triggers=[
            {"trigger_id": t.trigger_id, "type": t.trigger_type, "severity": t.severity, "summary": t.summary}
            for t in snap.triggers if t.status == "open"
        ][-max_items:],
        capabilities=snap.capabilities[-max_items:],
        memory=memory[:max_items],
        coverage=coverage[:max_items],
        intent=intent[:max_items],
        recent_findings=[
            {"type": f.finding_type, "summary": f.summary, "action": f.suggested_action}
            for f in snap.findings[-max_items:]
        ],
        truncated=(
            len(snap.observations) > max_items
            or len(snap.attempts) > max_items
            or len(snap.triggers) > max_items
        ),
    )


def _active_stage(snap: Snapshot) -> str:
    for a in reversed(snap.attempts):
        if a.stage:
            return a.stage
    return "start"


# ── Observation memory ──────────────────────────────────────────────────

def _build_memory(snap: Snapshot) -> list[MemoryEntry]:
    if not snap.observations:
        return []

    by_target: dict[str, list] = {}
    for obs in snap.observations:
        key = f"{obs.target}|{obs.metric}"
        by_target.setdefault(key, []).append(obs)

    entries = []
    for key, records in by_target.items():
        target, metric = key.split("|", 1)
        current = records[-1]
        prior = records[:-1]

        conflicts = [
            p for p in prior
            if isinstance(p.value, (int, float)) and isinstance(current.value, (int, float))
            and p.value is not None and current.value is not None
            and p.value * current.value < 0
        ]
        param_diffs = [p for p in prior if p.parameters != current.parameters]

        if conflicts:
            signal, summary = "conflict", f"{target}: conflicting prior value(s) — check contrast/parameters."
        elif param_diffs:
            signal, summary = "warning", f"{target}: parameter sensitivity detected across {len(param_diffs)} prior runs."
        elif len(records) == 1:
            signal, summary = "thin", f"{target}: single observation — more evidence needed."
        elif len(prior) >= 2:
            signal, summary = "agreement", f"{target}: consistent across {len(records)} measurements."
        else:
            signal, summary = "new", f"{target}: no prior data."

        entries.append(MemoryEntry(
            subject=target, metric=metric, current_value=current.value,
            prior_values=[{"value": p.value, "attempt": p.attempt_id, "contrast": p.contrast, "method": p.method} for p in prior[-5:]],
            signal=signal, summary=summary,
        ))

    return sorted(entries, key=lambda e: {"conflict": 0, "warning": 1, "thin": 2, "agreement": 3, "new": 4}.get(e.signal, 5))


def _build_coverage(snap: Snapshot) -> list[CoverageEntry]:
    by_target: dict[str, list] = {}
    for obs in snap.observations:
        by_target.setdefault(obs.target, []).append(obs)

    entries = []
    for target, records in by_target.items():
        methods = len({r.method for r in records if r.method})
        branches = len({r.branch_id for r in records if r.branch_id})
        contradictions = sum(
            1 for i, a in enumerate(records) for b in records[i + 1:]
            if isinstance(a.value, (int, float)) and isinstance(b.value, (int, float))
            and a.value is not None and b.value is not None and a.value * b.value < 0
        )
        if methods >= 2 and contradictions == 0:
            label = "convergent"
        elif contradictions > 0:
            label = "conflicted"
        elif methods >= 1:
            label = "adequate"
        elif len(records) == 0:
            label = "none"
        else:
            label = "thin"

        entries.append(CoverageEntry(
            subject=target, methods=methods, branches=branches,
            observations=len(records), contradictions=contradictions, label=label,
        ))

    return sorted(entries, key=lambda e: {"conflicted": 0, "thin": 1, "adequate": 2, "convergent": 3, "none": 4}.get(e.label, 5))


def _build_intent(snap: Snapshot) -> list[IntentEntry]:
    """Trace why each branch exists and whether it has drifted from the active goal."""
    entries = []
    active_goal = snap.goals[-1].text if snap.goals else snap.goal
    for branch in snap.branches:
        attempts = [a for a in snap.attempts if a.branch_id == branch.branch_id]
        if branch.reason == "main":
            intent, drift = "serve_goal", "low"
        elif branch.reason in ("parameter_sensitivity", "tool_alternative"):
            intent, drift = "explore", "medium"
        elif branch.reason == "biological_hypothesis":
            intent, drift = "explore", "high" if active_goal and "story" not in active_goal else "medium"
        elif branch.reason == "negative_pivot":
            intent, drift = "pivot", "high"
        else:
            intent, drift = "unknown", "medium"

        repair_attempts = [a for a in attempts if a.repair_count > 0 or a.parent_intervention]
        if repair_attempts:
            intent = "repair" if len(repair_attempts) > len(attempts) // 2 else intent

        summary = f"{branch.title or branch.branch_id}: {len(attempts)} attempts"
        if branch.reason != "main":
            summary += f" (reason: {branch.reason})"

        entries.append(IntentEntry(branch_id=branch.branch_id, intent=intent, drift=drift, summary=summary))
    return entries
