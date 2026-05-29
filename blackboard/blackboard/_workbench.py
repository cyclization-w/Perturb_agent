"""Workbench — the main entry point for LLM-driven analysis with provenance memory."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from blackboard._domain import Domain
from blackboard._schemas import (
    Event, Snapshot, Attempt, Outcome, Artifact, Observation,
    ReviewTrigger, Finding, Branch, Intervention, Interrupt, Conclusion,
    _now, _model_dump,
)
from blackboard._sandbox import run_code as _run_sandbox
from blackboard._safety import check as _safety_check
from blackboard._store import Store, validate_graph
from blackboard._context import compile_context
from blackboard._planner import plan_next_attempt, plan_intervention, review_outcome, _api_key, _anthropic_key


class Workbench:
    """LLM-driven analysis with provenance memory.

    Usage:
        wb = Workbench(domain=my_domain)
        wb.run("./data", goal="Analyze this dataset", steps=5)
        print(wb.status)
        wb.serve()
    """

    def __init__(self, domain: Domain, *, provider: str = "openai", sandbox: str = "subprocess", docker_image: str = ""):
        self.domain = domain
        self.provider = provider
        self.sandbox = sandbox
        self.docker_image = docker_image
        self._store: Store | None = None
        self._run_id: str = ""
        self._kernel = None  # persistent kernel — init on first use

    # ── Public API ────────────────────────────────────────────────────

    @property
    def status(self) -> dict:
        if not self._store: return {"state": "not_initialized"}
        snap = self._store.read_snapshot()
        if not snap: return {"state": "no_snapshot"}
        return {
            "run_id": snap.run_id, "phase": snap.phase, "workspace": snap.workspace, "goal": snap.goal,
            "attempts": len(snap.attempts), "observations": len(snap.observations),
            "conclusions": len(snap.conclusions),
            "triggers_open": len([t for t in snap.triggers if t.status == "open"]),
            "interrupts_open": len([i for i in snap.interrupts if i.status == "open"]),
            "branches": len(snap.branches),
        }

    @property
    def graph(self) -> dict | None:
        return self._store.read_graph() if self._store else None

    def run(self, workspace: str, *, goal: str = "", steps: int = 5) -> dict:
        self._init(workspace, goal)
        result = {"steps": 0, "stop_reason": "start"}
        for _ in range(steps):
            action = self._step()
            result["steps"] += 1
            if action in ("waiting_for_human", "complete", "blocked"):
                result["stop_reason"] = action
                break
        if not result.get("stop_reason"):
            result["stop_reason"] = "max_steps"
        return result

    def step(self, n: int = 1) -> list[str]:
        actions = []
        for _ in range(n):
            a = self._step()
            actions.append(a)
            if a in ("waiting_for_human", "complete", "blocked"):
                break
        return actions

    def answer(self, interrupt_id: str, response: str):
        """Answer a pending human interrupt."""
        snap = self._store.read_snapshot()
        for intr in snap.interrupts:
            if intr.interrupt_id == interrupt_id and intr.status == "open":
                self._emit("interrupt_resolved", {"interrupt_id": interrupt_id, "answer": response})
                # Also resolve the associated trigger
                if intr.trigger_id:
                    self._emit("trigger_resolved", {"trigger_id": intr.trigger_id, "answer": response})
                self._emit("run_resumed", {})
                return
        raise ValueError(f"No open interrupt found: {interrupt_id}")

    def report(self) -> dict:
        """Generate a complete analysis report with narrative synthesis."""
        from blackboard._reporting import generate_report, render_markdown, render_html

        snap = self._store.read_snapshot()
        ctx = compile_context(snap) if snap else None
        if not snap or not ctx:
            return {"error": "no_data"}

        report = generate_report(snap, ctx, provider=self.provider)

        # Write Markdown report to run directory
        if self._store:
            report_dir = self._store.run_dir
            md_path = report_dir / "report.md"
            md_path.write_text(render_markdown(report, graph_html_path="derivation_graph.html"), encoding="utf-8")

            graph = self._store.read_graph()
            html_path = report_dir / "report.html"
            html_path.write_text(render_html(report, graph), encoding="utf-8")

            json_path = report_dir / "report.json"
            json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

            report["paths"] = {
                "markdown": str(md_path),
                "html": str(html_path),
                "json": str(json_path),
            }

        return report

    def serve(self, port: int = 8765):
        from blackboard._api import create_app
        import uvicorn
        app = create_app(self)
        print(f"\n  Blackboard GUI: http://127.0.0.1:{port}")
        print(f"  API docs:       http://127.0.0.1:{port}/docs\n")
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

    # ── Engine ────────────────────────────────────────────────────────

    def _init(self, workspace: str, goal: str):
        run_id = f"run_{_now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
        run_dir = Path("runs") / run_id
        self._store = Store(run_dir)
        self._run_id = run_id

        self._emit("run_started", {"config": {
            "run_id": run_id, "workspace": workspace, "goal": goal,
            "domain": self.domain.name, "protocol": self.domain.protocol,
            "budget": {"max_attempts": 20, "max_branches": 3, "max_repairs": 3},
            "capabilities": self.domain.capabilities,
        }})
        # Auto-discover workspace files and inject as observations
        _scan_workspace(self, workspace)
        if goal:
            self._emit("goal_recorded", {"goal": {"goal_id": "goal_main", "text": goal, "status": "active"}})

    def _step(self) -> str:
        snap = self._store.read_snapshot()
        if not snap: return "no_snapshot"

        # 1. Blocking interrupts → stop
        if any(i.status == "open" for i in snap.interrupts):
            return "waiting_for_human"

        # 2. Execute active attempt
        active = next((a for a in snap.attempts if a.attempt_id == snap.active_attempt and a.status in ("planned", "running")), None)
        if active:
            self._execute_attempt(active)
            return "executed_attempt"

        # 3. Apply proposed intervention
        pending_intervention = next((i for i in snap.interventions if i.status == "proposed"), None)
        if pending_intervention:
            self._apply_intervention(pending_intervention)
            return "applied_intervention"

        # 4. Plan intervention for open triggers
        open_triggers = [t for t in snap.triggers if t.status == "open"]
        if open_triggers:
            try:
                ctx = compile_context(snap)
                proposal = plan_intervention(ctx, provider=self.provider) if _has_key(self.provider) else None
            except Exception:
                proposal = None
            if proposal:
                intervention = Intervention(
                    intervention_id=f"int_{uuid4().hex[:12]}",
                    trigger_id=proposal.trigger_id,
                    intervention_type=proposal.intervention_type,
                    summary=proposal.rationale,
                    target_ids=proposal.target_ids,
                    notebook_cells=[_model_dump(c) for c in proposal.notebook_cells],
                    params=proposal.params,
                    branch_reason=proposal.branch_reason,
                    created_at=_now().isoformat(),
                )
                self._emit("intervention_planned", {"intervention": _model_dump(intervention)})
                return "planned_intervention"
            return "blocked"

        # 5. Generate conclusions if analysis is done
        ctx = compile_context(snap)
        if ctx.budget_remaining.get("attempts", 0) <= 0 or self._all_stages_completed(snap):
            self._generate_conclusions(snap, ctx)
            self._emit("run_complete", {})
            return "complete"

        # 6. Plan next attempt
        try:
            proposal = plan_next_attempt(ctx, self.domain.name, provider=self.provider) if _has_key(self.provider) else None
        except Exception as exc:
            import sys
            print(f"[workbench] plan_next_attempt failed: {exc}", file=sys.stderr)
            self._emit("finding_recorded", {"finding": _model_dump(Finding(
                finding_id=f"fnd_{uuid4().hex[:12]}",
                finding_type="error", severity="warning", suggested_action="rerun",
                summary=f"Plan generation failed: {str(exc)[:200]}. Retrying…",
            ))})
            proposal = None

        if proposal and self._validate_proposal(proposal, snap):
            attempt = Attempt(
                attempt_id=f"att_{uuid4().hex[:12]}",
                branch_id=snap.active_branch,
                title=proposal.title or "Analysis step",
                objective=proposal.objective or "Analyze data",
                stage=proposal.stage or "inspect",
                capability_ids=proposal.capability_ids,
                notebook_cells=[_model_dump(c) for c in proposal.notebook_cells],
                expected_artifacts=proposal.expected_artifacts,
                required_validators=proposal.required_validators,
                parameters=proposal.parameters, rationale=proposal.rationale,
            )
            self._emit("attempt_planned", {"attempt": _model_dump(attempt)})
            return "planned_attempt"

        self._emit("run_complete", {})
        return "complete"

    def _emit(self, event_type: str, payload: dict):
        e = Event(event_id=f"evt_{uuid4().hex[:12]}", event_type=event_type, run_id=self._run_id, payload=payload)
        if self._store.acquire_lease("engine", ttl_seconds=60):
            self._store.append([e])
            # Validate graph after write
            graph = self._store.read_graph()
            if graph:
                violations = validate_graph(graph)
                if violations:
                    import sys
                    print(f"[workbench] Graph validation found {len(violations)} violation(s)", file=sys.stderr)
                    for v in violations[:5]:
                        print(f"  {v}", file=sys.stderr)
            self._store.release_lease("engine")

    def _all_stages_completed(self, snap: Snapshot) -> bool:
        completed = {a.stage for a in snap.attempts if a.status == "succeeded"}
        agenda_stages = {s for s in self.domain.agenda if s != "report"}
        return agenda_stages and agenda_stages.issubset(completed)

    # ── Validation ────────────────────────────────────────────────────

    def _validate_proposal(self, proposal, snap: Snapshot) -> bool:
        """Structural validation: budget, capability existence, repair limit."""
        if snap.budget and len(snap.attempts) >= snap.budget.max_attempts:
            self._emit("trigger_opened", {"trigger": _model_dump(ReviewTrigger(
                trigger_id=f"trg_{uuid4().hex[:12]}",
                trigger_type="budget_exhausted", severity="warning",
                summary=f"Attempt budget ({snap.budget.max_attempts}) exhausted.",
            ))})
            return False

        domain_cap_ids = {c.get("id") for c in self.domain.capabilities if c.get("id")}
        unknown = [cid for cid in proposal.capability_ids if cid not in domain_cap_ids]
        if unknown:
            self._emit("finding_recorded", {"finding": _model_dump(Finding(
                finding_id=f"fnd_{uuid4().hex[:12]}",
                finding_type="missing_context", severity="warning",
                suggested_action="change_params",
                summary=f"Unknown capabilities: {', '.join(unknown)}",
            ))})
            return False

        # Check repair budget
        current_repairs = sum(1 for a in snap.attempts if a.repair_count > 0 and a.branch_id == snap.active_branch)
        if current_repairs >= 3:
            self._emit("interrupt_opened", {"interrupt": _model_dump(Interrupt(
                interrupt_id=f"irq_{uuid4().hex[:12]}",
                source="budget_exhausted",
                question="Repair budget exhausted for this branch. Continue, stop branch, or open new branch?",
                options=["stop_branch", "open_branch", "continue_anyway"],
                default_action="ask_user",
            ))})
            return False

        return True

    # ── Execution ─────────────────────────────────────────────────────

    def _execute_attempt(self, attempt: Attempt):
        snap = self._store.read_snapshot()
        artifacts_dir = self._store.run_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        code = _build_notebook_code(attempt, snap.workspace, str(artifacts_dir), self.domain.audit_preamble)

        # Safety check
        violations = _safety_check(code, workspace=snap.workspace, artifacts_dir=str(artifacts_dir))
        if violations:
            msg = "Safety violations: " + "; ".join(violations[:3])
            outcome = Outcome(outcome_id=f"out_{attempt.attempt_id}", attempt_id=attempt.attempt_id, status="error", summary=msg, metrics={"safety_violations": violations})
            self._emit("outcome_recorded", {"outcome": _model_dump(outcome)})
            self._emit("trigger_opened", {"trigger": _model_dump(ReviewTrigger(
                trigger_id=f"trg_{uuid4().hex[:12]}", attempt_id=attempt.attempt_id,
                trigger_type="runtime_error", severity="blocking", summary=msg,
            ))})
            return

        # Execute in persistent kernel (like CellVoyager). Fallback to subprocess.
        if self.sandbox == "subprocess":
            # Use persistent kernel for variable persistence across cells
            if self._kernel is None:
                from blackboard._kernel import KernelSession
                self._kernel = KernelSession(snap.workspace, str(artifacts_dir))
            try:
                result = self._kernel.execute(attempt.attempt_id, code)
                # Auto-restart on crash
                if result.get("returncode", 0) != 0 and not self._kernel.alive():
                    self._kernel.restart()
            except Exception:
                # Fallback to subprocess
                result = _run_sandbox(code, snap.workspace, str(artifacts_dir), backend="subprocess")
        else:
            # Docker mode — no kernel, use sandbox
            result = _run_sandbox(code, snap.workspace, str(artifacts_dir), backend=self.sandbox, docker_image=self.docker_image)

        # Append cell to single execution notebook
        _append_notebook(self._store.run_dir, attempt, code, result)

        # Deterministic safety floor — always runs
        outcome_status = "success" if result["returncode"] == 0 else "error"
        if result.get("timed_out"):
            outcome_status = "error"
        if "Traceback" in (result.get("stderr", "") or ""):
            outcome_status = "error"

        outcome = Outcome(
            outcome_id=f"out_{attempt.attempt_id}",
            attempt_id=attempt.attempt_id, status=outcome_status,
            summary=(result.get("stderr") or result.get("stdout") or "Executed")[-500:],
            metrics=result,
        )
        self._emit("outcome_recorded", {"outcome": _model_dump(outcome)})

        # Read manifest → register artifacts + observations
        manifest_path = artifacts_dir / f"{attempt.attempt_id}_manifest.json"
        obs_count = 0
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for i, a in enumerate(manifest.get("artifacts", []) or []):
                    self._emit("artifact_registered", {"artifact": _model_dump(Artifact(
                        artifact_id=f"art_{attempt.attempt_id}_{i}", attempt_id=attempt.attempt_id,
                        path=a.get("path", ""), kind=a.get("kind", ""), summary=a.get("summary", ""),
                        metadata=a.get("metadata", {}),
                    ))})
                for i, o in enumerate(manifest.get("observations", []) or []):
                    obs_count += 1
                    self._emit("observation_registered", {"observation": _model_dump(Observation(
                        observation_id=f"obs_{attempt.attempt_id}_{i}",
                        type=o.get("type", "custom"), target=o.get("target", ""), metric=o.get("metric", ""),
                        value=o.get("value"), contrast=o.get("contrast", ""), method=o.get("method", ""),
                        parameters=o.get("parameters", {}), uncertainty=o.get("uncertainty", {}),
                        attempt_id=attempt.attempt_id, branch_id=attempt.branch_id,
                    ))})
            except Exception:
                pass

        # Mechanical safety triggers
        if outcome_status == "error":
            stderr_preview = (result.get("stderr") or result.get("stdout") or "")[-500:]
            self._emit("trigger_opened", {"trigger": _model_dump(ReviewTrigger(
                trigger_id=f"trg_{uuid4().hex[:12]}", attempt_id=attempt.attempt_id,
                trigger_type="runtime_error", severity="blocking",
                summary=f"Execution failed (code {result.get('returncode')}). Error: {stderr_preview}",
            ))})
        if obs_count == 0 and outcome_status == "success":
            self._emit("finding_recorded", {"finding": _model_dump(Finding(
                finding_id=f"fnd_{uuid4().hex[:12]}", attempt_id=attempt.attempt_id,
                finding_type="missing_context", severity="warning",
                suggested_action="rerun",
                summary="No observations registered. LLM code may not have called register_observation().",
            ))})

        # ── Tool-use loop: LLM inspects results before next cell ────────
        ctx = compile_context(self._store.read_snapshot())
        findings = []
        if _has_key(self.provider):
            try:
                snap_after = self._store.read_snapshot()
                recent_obs = [
                    {"type": o.type, "target": o.target, "metric": o.metric, "value": o.value,
                     "contrast": o.contrast, "method": o.method}
                    for o in snap_after.observations[-20:]
                ]
                findings, _ = _tool_loop(
                    outcome_summary=outcome.summary,
                    result=result,
                    obs_count=obs_count,
                    recent_obs=recent_obs,
                    snap=snap_after,
                    provider=self.provider,
                )
            except Exception:
                pass

        for f in findings:
            self._emit("finding_recorded", {"finding": _model_dump(Finding(
                finding_id=f"fnd_{uuid4().hex[:12]}", attempt_id=attempt.attempt_id,
                finding_type=f.get("finding_type", "continue_ok"),
                severity=f.get("severity", "info"),
                suggested_action=f.get("suggested_action", "continue"),
                summary=f.get("summary", ""),
            ))})
            if f.get("severity") in ("warning", "blocking"):
                self._emit("trigger_opened", {"trigger": _model_dump(ReviewTrigger(
                    trigger_id=f"trg_{uuid4().hex[:12]}", attempt_id=attempt.attempt_id,
                    trigger_type=f.get("finding_type", ""),
                    severity=f.get("severity", "warning"),
                    summary=f.get("summary", ""),
                ))})

    # ── Intervention ──────────────────────────────────────────────────

    def _apply_intervention(self, intervention: Intervention):
        snap = self._store.read_snapshot()

        # Resolve the trigger
        if intervention.trigger_id:
            self._emit("trigger_resolved", {"trigger_id": intervention.trigger_id})

        # Branch operations
        if intervention.intervention_type == "open_branch":
            if len([b for b in snap.branches if b.status == "active"]) >= 3:
                return  # budget exceeded
            new_branch = Branch(
                branch_id=f"br_{uuid4().hex[:8]}",
                title=intervention.summary[:60],
                parent_id=snap.active_branch,
                reason=intervention.branch_reason or "tool_alternative",
            )
            self._emit("branch_opened", {"branch": _model_dump(new_branch)})
            self._emit("intervention_applied", {"intervention_id": intervention.intervention_id})
            return

        if intervention.intervention_type == "stop_branch":
            self._emit("branch_stopped", {"branch_id": snap.active_branch})
            self._emit("intervention_applied", {"intervention_id": intervention.intervention_id})
            return

        # Ask user
        if intervention.intervention_type == "ask_user":
            # If there's a trigger with a summary, use it as context
            trigger_summary = ""
            if intervention.trigger_id:
                for t in snap.triggers:
                    if t.trigger_id == intervention.trigger_id:
                        trigger_summary = t.summary
                        break
            question = intervention.summary or trigger_summary or "Analysis hit a problem. Check the result above and provide guidance."
            self._emit("interrupt_opened", {"interrupt": _model_dump(Interrupt(
                interrupt_id=f"irq_{uuid4().hex[:12]}",
                source="critic_review",
                trigger_id=intervention.trigger_id,
                question=question,
                default_action="ask_user",
            ))})
            return

        # Create follow-up attempt for computable interventions
        parent = next((a for a in snap.attempts if a.attempt_id == snap.active_attempt), None)
        follow_up = Attempt(
            attempt_id=f"att_{uuid4().hex[:12]}",
            branch_id=snap.active_branch,
            title=f"Fix: {intervention.intervention_type}",
            objective=intervention.summary,
            stage=parent.stage if parent else "",
            parent_ids=[parent.attempt_id] if parent else [],
            parent_intervention=intervention.intervention_id,
            notebook_cells=intervention.notebook_cells or ([_model_dump(c) for c in parent.notebook_cells] if parent else []),
            capability_ids=parent.capability_ids if parent else [],
            parameters=intervention.params,
            repair_count=(parent.repair_count + 1) if parent and intervention.intervention_type == "fix_code" else 0,
            rationale=intervention.summary,
        )
        self._emit("intervention_applied", {"intervention_id": intervention.intervention_id})
        self._emit("attempt_planned", {"attempt": _model_dump(follow_up)})

    # ── Conclusions ───────────────────────────────────────────────────

    def _generate_conclusions(self, snap: Snapshot, ctx):
        """Generate conclusions from accumulated observations and findings."""
        if not _api_key() or not snap.observations:
            return

        coverage_summary = [
            {"subject": c.subject, "methods": c.methods, "contradictions": c.contradictions, "label": c.label}
            for c in ctx.coverage
        ]
        memory_summary = [
            {"subject": m.subject, "signal": m.signal, "value": m.current_value}
            for m in ctx.memory if m.signal in ("agreement", "conflict")
        ]

        try:
            conclusion_text = _generate_conclusion_text(ctx.goal or snap.goal, coverage_summary, memory_summary)
            conclusion = Conclusion(
                conclusion_id=f"con_{uuid4().hex[:12]}",
                text=conclusion_text.get("text", ""),
                grade=conclusion_text.get("grade", "tentative"),
                support_ids=[obs.observation_id for obs in snap.observations[-10:]],
            )
            self._emit("conclusion_recorded", {"conclusion": _model_dump(conclusion)})
        except Exception:
            pass


def _generate_conclusion_text(goal: str, coverage: list, memory: list) -> dict:
    from blackboard._planner import _call_llm
    prompt = json.dumps({"goal": goal, "coverage": coverage, "key_findings": memory}, ensure_ascii=False)[:4000]
    schema = {
        "type": "object", "properties": {
            "text": {"type": "string"},
            "grade": {"type": "string", "enum": ["robust", "supported", "tentative", "inconclusive", "negative"]},
        }, "required": ["text", "grade"], "additionalProperties": False,
    }
    return _call_llm(
        "You are a scientific reviewer. Based on the evidence summary, draft a concise conclusion with a grade.",
        prompt, schema,
    )


# ── Notebook execution ──────────────────────────────────────────────────

def _build_notebook_code(attempt: Attempt, workspace: str, artifacts_dir: str, audit_preamble: str) -> str:
    preamble = audit_preamble or _DEFAULT_PREAMBLE
    preamble = preamble.replace("{workspace}", workspace).replace("{artifacts_dir}", artifacts_dir)
    preamble = preamble.replace('"attempt_id": ""', f'"attempt_id": "{attempt.attempt_id}"')
    cells = [preamble]
    for c in attempt.notebook_cells:
        cells.append(c.get("source", "") if isinstance(c, dict) else c.source)
    return "\n\n# %% CELL\n\n".join(cells)


_DEFAULT_PREAMBLE = '''
import atexit, json, sys
from pathlib import Path

workspace = Path("{workspace}")
artifacts_dir = Path("{artifacts_dir}")
artifacts_dir.mkdir(parents=True, exist_ok=True)

_manifest = {"manifest_id": "manifest", "attempt_id": "", "artifacts": [], "observations": []}

def register_artifact(path, kind, summary="", metadata=None):
    _manifest["artifacts"].append({"path": str(path), "kind": str(kind), "summary": str(summary), "metadata": dict(metadata or {})})

def register_observation(type, target="", metric="", value=None, contrast="", method="", parameters=None, uncertainty=None):
    _manifest["observations"].append({"type": str(type), "target": str(target), "metric": str(metric), "value": value, "contrast": str(contrast), "method": str(method), "parameters": dict(parameters or {}), "uncertainty": dict(uncertainty or {})})

def _flush():
    path = artifacts_dir / (_manifest.get("attempt_id", "unknown") + "_manifest.json")
    path.write_text(json.dumps(_manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("MANIFEST: " + str(path), file=sys.stderr)

atexit.register(_flush)
print("Audit contract ready.", file=sys.stderr)
'''


def _append_notebook(run_dir: Path, attempt: Attempt, code: str, result: dict):
    """Append a cell to the single execution notebook (like CellVoyager)."""
    try:
        import nbformat as nbf
        from nbformat.v4 import new_code_cell, new_markdown_cell, new_output

        nb_dir = run_dir / "notebooks"
        nb_dir.mkdir(parents=True, exist_ok=True)
        nb_path = nb_dir / "execution.ipynb"

        if nb_path.exists():
            nb = nbf.read(nb_path, as_version=4)
        else:
            nb = nbf.v4.new_notebook(metadata={
                "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                "language_info": {"name": "python", "pygments_lexer": "ipython3"},
            })

        # Add markdown cell for attempt header
        nb.cells.append(new_markdown_cell(
            f"## Cell {len([c for c in nb.cells if c.cell_type == 'markdown']) + 1} — {attempt.stage or 'analysis'}\n"
            f"**{attempt.title or 'Step'}** — {attempt.objective or ''}"
        ))

        # Add code cell
        cell = new_code_cell(code)
        if result.get("stdout"):
            cell.outputs.append(new_output("stream", name="stdout", text=result["stdout"][-3000:] or ""))
        if result.get("stderr"):
            cell.outputs.append(new_output("stream", name="stderr", text=result["stderr"][-3000:] or ""))
        if result.get("returncode", 0) != 0:
            import sys
            cell.outputs.append(new_output("error", ename="RuntimeError",
                evalue=f"Return code: {result.get('returncode')}",
                traceback=[(result.get('stderr', '') or 'Unknown error').split('\n')[0]]))
        nb.cells.append(cell)

        nbf.write(nb, nb_path)
    except ImportError:
        import sys
        print("[workbench] nbformat not installed — skipping .ipynb output. pip install nbformat", file=sys.stderr)


def _tool_loop(outcome_summary: str, result: dict, obs_count: int, recent_obs: list, snap, provider: str) -> tuple[list, str]:
    """Let the LLM inspect execution results with tools before planning next.

    After code execution, the LLM can call view_plot, read_file, search_web,
    etc. to understand what happened. It then generates findings and a
    decision about what to do next (continue / fix / ask_user / done).
    """
    from blackboard._tools import execute_tool, tool_schemas
    from blackboard._planner import _call_llm, _api_key

    system = (
        "You are a scientific reviewer inspecting the output of one notebook cell execution. "
        "You have tools available. Use them to inspect results. Then decide what to do next.\n\n"
        "Decision options:\n"
        "- continue: result is good, proceed to next cell\n"
        "- fix: result is wrong, suggest what to change\n"
        "- ask_user: need human input (control label, design question)\n"
        "- done: analysis is complete"
    )

    user = json.dumps({
        "outcome": {"summary": outcome_summary, "status": result.get("returncode"), "observations_count": obs_count},
        "recent_observations": recent_obs,
        "artifacts": [{"path": a.path, "kind": a.kind, "summary": a.summary} for a in snap.artifacts[-6:]],
    }, ensure_ascii=False, default=str)[:4000]

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user + "\n\nUse tools to inspect results. Then set your decision."},
    ]

    for _ in range(4):  # max 4 tool calls
        response = _call_openai_tools(messages, tool_schemas(), provider)
        if not response.get("tool_calls"):
            break
        for tc in response.get("tool_calls", []):
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"])
            result_text = json.dumps(execute_tool(name, args, snap=snap), ensure_ascii=False, default=str)[:3000]
            messages.append({"role": "assistant", "content": None, "tool_calls": [tc]})
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_text})

    # Final call: get findings + decision
    messages.append({"role": "user", "content": "Now return your findings and decision as JSON."})
    final = _call_openai_tools(messages, [], provider, tool_choice="none")
    try:
        decision = json.loads(_extract_json_resp(final.get("content", "{}")))
    except Exception:
        decision = {"decision": "continue", "findings": []}

    findings = [{
        "finding_type": f.get("type", "continue_ok"),
        "severity": f.get("severity", "info"),
        "suggested_action": f.get("action", "continue"),
        "summary": f.get("summary", ""),
    } for f in decision.get("findings", [])]
    return findings, decision.get("decision", "continue")


def _call_openai_tools(messages: list, tools: list, provider: str, tool_choice: str = "auto"):
    """Call OpenAI with tool support. Falls back to chat completions."""
    from blackboard._planner import _api_key, _anthropic_key, _model
    key = _api_key() if provider == "openai" else _anthropic_key()
    if not key:
        return {"content": "{}"}

    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url=os.getenv("OPENAI_BASE_URL") or None)
        kwargs = {
            "model": _model(provider),
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 4096,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        response = client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        result = {"content": msg.content or ""}
        if msg.tool_calls:
            result["tool_calls"] = [{"id": tc.id, "type": tc.type, "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in msg.tool_calls]
        return result
    except Exception:
        return {"content": '{"decision": "continue", "findings": []}'}


def _extract_json_resp(text: str) -> dict:
    import re
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    return json.loads(text[start:end + 1]) if start >= 0 and end > start else {}


def _scan_workspace(wb, workspace: str):
    """List workspace files. LLM decides what to load and how."""
    p = Path(workspace)
    if not p.exists():
        return
    for f in sorted(p.iterdir()):
        if f.name.startswith("."):
            continue
        if f.is_dir():
            wb._emit("observation_registered", {"observation": _model_dump(Observation(
                observation_id=f"obs_ws_{uuid4().hex[:8]}",
                type="workspace_file", target=f"{f.name}/", metric="directory",
                value=f.name, method="auto_discover",
                attempt_id="", branch_id="main",
            ))})
        else:
            wb._emit("observation_registered", {"observation": _model_dump(Observation(
                observation_id=f"obs_ws_{uuid4().hex[:8]}",
                type="workspace_file", target=f.name, metric="file_size",
                value=f.stat().st_size, method="auto_discover",
                attempt_id="", branch_id="main",
            ))})


def _has_key(provider: str) -> bool:
    if provider == "anthropic":
        return bool(_anthropic_key())
    return bool(_api_key())
