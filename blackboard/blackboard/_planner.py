"""LLM planner — calls OpenAI to generate analysis plans and interventions.

API key: set OPENAI_API_KEY env var or ~/.blackboard/config.json
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import uuid4

from blackboard._context import Context
from blackboard._schemas import AttemptProposal, InterventionProposal, NotebookCell


def _config() -> dict:
    cfg = Path.home() / ".blackboard" / "config.json"
    return json.loads(cfg.read_text()) if cfg.exists() else {}


def _api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY") or _config().get("openai_api_key")


def _anthropic_key() -> str | None:
    return os.getenv("ANTHROPIC_API_KEY") or _config().get("anthropic_api_key")


def _model(provider: str = "openai") -> str:
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-5"
    return os.getenv("OPENAI_MODEL") or "gpt-5.2"


def _call_llm(system: str, user: str, output_schema: dict, *, provider: str = "openai") -> dict:
    if provider == "anthropic":
        return _call_anthropic(system, user, output_schema)
    return _call_openai(system, user, output_schema)


def _call_openai(system: str, user: str, output_schema: dict) -> dict:
    from openai import OpenAI
    key = _api_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set.")
    client = OpenAI(api_key=key, base_url=os.getenv("OPENAI_BASE_URL") or None)
    response = client.chat.completions.create(
        model=_model("openai"),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user + "\n\nReturn only a valid JSON object. No markdown, no explanation."},
        ],
        temperature=0.1, max_tokens=8192,
    )
    text = response.choices[0].message.content.strip()
    return _extract_json(text)


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM output, handle markdown and repair truncation."""
    text = text.strip()
    import re
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Repair: close unclosed strings and braces
    repaired = _repair_json(text)
    return json.loads(repaired)


def _repair_json(text: str) -> str:
    """Attempt to repair truncated/malformed JSON."""
    # Remove trailing incomplete content
    lines = text.split("\n")
    # Close unclosed strings in the last line
    if lines:
        last = lines[-1]
        quote_count = last.count('"') - last.count('\\"')
        if quote_count % 2 != 0:
            lines[-1] = last + '"'
    text = "\n".join(lines)
    # Count braces and close them
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    text += "}" * max(0, open_braces)
    text += "]" * max(0, open_brackets)
    return text


def _call_anthropic(system: str, user: str, output_schema: dict) -> dict:
    from anthropic import Anthropic
    key = _anthropic_key()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set.")
    client = Anthropic(api_key=key, base_url=os.getenv("ANTHROPIC_BASE_URL") or None)
    prompt = f"{user}\n\nReturn ONLY valid JSON matching this schema:\n{json.dumps(output_schema, ensure_ascii=False, indent=2)}"
    response = client.messages.create(
        model=_model("anthropic"),
        system=system,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1, max_tokens=8192,
    )
    text = response.content[0].text if response.content else ""
    return _extract_json(text)


# ── Planner ─────────────────────────────────────────────────────────────

_SYSTEM = """You are the Main Analyst in a scientific workbench. You think and act one step at a time.

WORKFLOW: You generate ONE executable cell. The harness runs it immediately and shows you the output. Then you generate the next cell, informed by what you just saw. This repeats until the analysis is complete.

CRITICAL RULES:
1. Generate exactly ONE notebook cell per turn. Keep it under 150 lines.
2. Call register_observation() for EVERY quantitative finding.
   register_observation(type, target, metric, value, contrast, method, parameters, uncertainty)
3. Call register_artifact(path, kind, summary) for each output file.
4. Read workspace files (read-only). Write outputs only through artifacts_dir.
5. AFTER each cell executes, you see: stdout/stderr + newly registered observations + memory signals (conflicts, warnings, agreements). Use this to decide your next cell.
6. Check context.memory FIRST. If any entry has signal "conflict", address it.
7. You decide when to move to the next stage or when the analysis is complete.
"""

_SPECIALISTS = {
    "guide_qc": (
        "You specialize in guide assignment and target mapping quality control. "
        "Given guide count data, target mapping tables, and assignment threshold "
        "configurations, you detect: low target coverage, guide discordance, "
        "threshold sensitivity, and single-guide-driven effects. "
        "Return a structured assessment with concrete recommended parameters."
    ),
    "de_testing": (
        "You specialize in differential expression analysis for Perturb-seq. "
        "Given DE results, contrast definitions, batch-condition crosstabs, and "
        "method choices (wilcoxon, t-test, DESeq2), you detect: wrong contrast, "
        "batch confounding, method mismatch, parameter sensitivity, and empty/weak "
        "results. Return a structured assessment with corrected parameters."
    ),
    "pathway": (
        "You specialize in pathway and gene module enrichment analysis. "
        "Given gene lists, DE results, module scores, and pathway database results, "
        "you detect: database version issues, over-interpretation, missing modules, "
        "and co-regulated module patterns. Return a structured assessment."
    ),
    "general": (
        "You are a scientific specialist. Analyze the provided data and return "
        "a structured assessment with findings and recommendations."
    ),
}

_USER_TEMPLATE = """Task: {task}

Context:
{context}

{instruction}

Return a {decision_type} decision in strict JSON format."""


def call_specialist(role: str, task: str, data: dict, *, provider: str = "openai") -> dict:
    """Delegate a sub-task to a domain specialist.

    The specialist gets a focused system prompt, the relevant data
    (observations, artifact summaries, parameters), and returns a
    structured assessment. The main planner incorporates this into
    its plan.
    """
    system = _SPECIALISTS.get(role, _SPECIALISTS["general"])
    user = json.dumps({"task": task, "data": data}, ensure_ascii=False, default=str)[:6000]

    schema = {
        "type": "object", "properties": {
            "assessment": {"type": "string"},
            "findings": {"type": "array", "items": {"type": "object", "properties": {
                "severity": {"type": "string", "enum": ["info", "warning", "blocking"]},
                "subject": {"type": "string"},
                "detail": {"type": "string"},
            }, "required": ["severity", "subject", "detail"], "additionalProperties": False}},
            "recommended_params": {"type": "object"},
            "recommended_actions": {"type": "array", "items": {"type": "string"}},
        }, "required": ["assessment"], "additionalProperties": False,
    }

    result = _call_llm(system, user, schema, provider=provider)
    return result


def _choose_specialist(stage: str, triggers: list[dict]) -> str:
    """Map stage and trigger context to specialist role."""
    stage_map = {
        "guide_assignment": "guide_qc",
        "target_qc": "guide_qc",
        "perturbation_validation": "de_testing",
        "effect_exploration": "de_testing",
        "target_discovery": "pathway",
        "state_reference": "pathway",
    }
    if stage in stage_map:
        return stage_map[stage]
    trigger_types = [t.get("type", "") for t in triggers]
    if any("guide" in t or "coverage" in t for t in trigger_types):
        return "guide_qc"
    if any("contrast" in t or "de" in t.lower() or "batch" in t for t in trigger_types):
        return "de_testing"
    if any("pathway" in t or "module" in t or "biology" in t for t in trigger_types):
        return "pathway"
    return "general"


def plan_next_attempt(ctx: Context, domain_name: str, *, provider: str = "openai") -> AttemptProposal:
    """Generate plan + code in ONE LLM call.

    The LLM receives the full context (memory, coverage, previous findings,
    capabilities, domain rubric) and returns both a plan and executable code
    in a single response. No JSON — plan and code are parsed from plain text.
    """
    # Inject FULL previous cell output — like CellVoyager critique
    prev_output = ""
    for f in ctx.recent_findings[-3:]:
        prev_output += f"\nPrev: [{f.get('type', '')}] {f.get('summary', '')}"

    prompt = (
        "You are a PI-guided Perturb-seq analyst. You work ONE cell at a time.\n"
        + "The harness executes your cell and shows you the full output. You decide the next step.\n\n"
        + "RULES:\n"
        + "- Distinguish annotation audit from de novo assignment.\n"
        + "- Distinguish matrix QC from FASTQ preprocessing.\n"
        + "- If control label/perturbation type is unknown, ask — don't guess.\n"
        + "- Calibrate claims: 'QC audit', 'coverage summary', 'sanity check'.\n"
        + "  Avoid: 'validated biology', 'proved mechanism', 'full analysis'.\n"
        + "- Reuse kernel state: variables and imports persist across cells.\n"
        + "- Only import: scanpy, anndata, pertpy, pandas, numpy, scipy, matplotlib, seaborn, sklearn, statsmodels.\n\n"
        + (f"PROTOCOL:\n{ctx.protocol}\n\n" if ctx.protocol else "")
        + "WORKSPACE:\n"
        + "\n".join(f"  {f['subject']} ({f['value']} bytes)" if f.get("metric") == "file_size" else f"  {f['subject']}"
            for f in ctx.workspace_files) + "\n\n"
        + f"Goal: {ctx.goal}\n"
        + f"Attempts: {ctx.attempts_done}\n\n"
        + "MEMORY:\n"
        + "\n".join(f"  [{m.signal}] {m.subject}: {m.summary}" for m in ctx.memory[:5]) + "\n\n"
        + "PREVIOUS:" + (prev_output or " (first cell)") + "\n\n"
        + "TRIGGERS:\n"
        + "\n".join(f"  [{t.get('severity','')}] {t.get('summary','')[:200]}" for t in ctx.open_triggers[:3]) + "\n\n"
        + "FORMAT:\n"
        + "---PLAN---\n"
        + "title: <short>\n"
        + "stage: <stage name>\n"
        + "objective: <one sentence>\n"
        + "---CODE---\n"
        + "<Python code — ONE cell, under 150 lines>\n"
        + "---END---\n\n"
        + "RULES: ONE cell. Call register_observation() and register_artifact().\n"
    )

    try:
        if provider == "anthropic":
            from anthropic import Anthropic
            client = Anthropic(api_key=_anthropic_key(), base_url=os.getenv("ANTHROPIC_BASE_URL") or None)
            response = client.messages.create(
                model=_model("anthropic"), system="", messages=[{"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=8192,
            )
            text = response.content[0].text if response.content else ""
        else:
            from openai import OpenAI
            client = OpenAI(api_key=_api_key(), base_url=os.getenv("OPENAI_BASE_URL") or None)
            response = client.chat.completions.create(
                model=_model("openai"), messages=[{"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=8192,
            )
            text = response.choices[0].message.content.strip()
    except Exception:
        text = "---PLAN---\ntitle: Fallback\nstage: inspect\nobjective: Basic inspection\n---CODE---\nimport os\nprint('Workspace:', os.environ.get('WORKSPACE'))\nregister_observation('qc','status','note',value='planned',method='fallback')\n---END---"

    plan, cells = _parse_plan_code(text)
    return AttemptProposal(
        proposal_id=f"prop_{uuid4().hex[:12]}",
        title=plan.get("title", ""), objective=plan.get("objective", ""),
        stage=plan.get("stage", ""), capability_ids=plan.get("capability_ids", []),
        notebook_cells=cells,
        expected_artifacts=plan.get("expected_artifacts", []),
        required_validators=plan.get("required_validators", []),
        rationale=plan.get("rationale", ""),
    )


def _parse_plan_code(text: str) -> tuple[dict, list]:
    """Parse the ---PLAN---/---CODE---/---END--- format."""
    import re
    plan = {}
    cells = []

    # Extract plan section
    plan_match = re.search(r"---PLAN---\s*\n(.*?)---CODE---", text, re.DOTALL)
    if plan_match:
        for line in plan_match.group(1).strip().split("\n"):
            line = line.strip()
            if ":" in line:
                key, _, value = line.partition(":")
                plan[key.strip()] = value.strip()
    else:
        plan = {"title": "Analysis", "stage": "inspect", "objective": "Analyze data"}

    # Extract code section
    code_match = re.search(r"---CODE---\s*\n(.*?)---END---", text, re.DOTALL | re.IGNORECASE)
    if code_match:
        code_text = code_match.group(1).strip()
        # Remove markdown fences if any
        code_text = re.sub(r"```(?:python)?\s*", "", code_text)
        code_text = code_text.strip()
        if code_text:
            cells.append(NotebookCell(source=code_text, role="execute", title=plan.get("title", "")))

    if not cells:
        # Fallback: try to extract any code from the response
        code_match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
        if code_match:
            cells.append(NotebookCell(source=code_match.group(1).strip(), role="execute", title="Fallback"))
        else:
            cells.append(NotebookCell(source="import os\nprint('Workspace:', os.environ.get('WORKSPACE', '.'))\nregister_observation('status','check','note',value='ok',method='inspect')", role="execute", title="Fallback"))

    return plan, cells


def _parse_code_cells(text: str) -> list:
    """Parse plain text code into notebook cells. Split by ===CELL=== delimiter."""
    import re
    # Remove markdown fences if any
    text = re.sub(r"```(?:python)?\s*", "", text)
    text = text.strip()
    cells = []
    parts = re.split(r"===CELL===", text)
    for part in parts:
        part = part.strip()
        if not part or part.startswith("#"):
            continue
        # First line might be a title
        lines = part.split("\n")
        title = ""
        if lines[0].startswith("# "):
            title = lines[0][2:].strip()
            source = "\n".join(lines[1:])
        else:
            source = part
        if source.strip():
            cells.append(NotebookCell(source=source.strip(), role="execute", title=title))
    return cells


_CODE_SYSTEM = """You write ONE executable Python cell for scientific analysis.

The harness provides: workspace (read-only), artifacts_dir (writable).
Required: call register_observation() for every quantitative finding.
Required: call register_artifact() for every output file.

IMPORTANT: Write exactly ONE cell. Keep it under 150 lines.
Use standard libraries: scanpy, anndata, pandas, numpy, scipy, matplotlib, seaborn.

After this cell runs, you will see its stdout, stderr, and any registered observations.
Use that feedback to write the next cell."""


def plan_intervention(ctx: Context, *, provider: str = "openai") -> InterventionProposal:
    triggers = json.dumps(ctx.open_triggers, ensure_ascii=False)[:3000]
    instruction = f"Open triggers: {triggers}. Plan a minimal intervention. For fix_code, provide corrected notebook cells."
    user = _USER_TEMPLATE.format(task="plan_intervention", context=json.dumps(_serialize_ctx(ctx), ensure_ascii=False, indent=2)[:8000], instruction=instruction, decision_type="intervention_proposal")

    schema = {
        "type": "object", "properties": {
            "intervention_proposal": {
                "type": "object", "properties": {
                    "trigger_id": {"type": "string"}, "intervention_type": {"type": "string"},
                    "target_ids": {"type": "array", "items": {"type": "string"}},
                    "notebook_cells": {"type": "array", "items": {
                        "type": "object", "properties": {"source": {"type": "string"}, "role": {"type": "string"}, "title": {"type": "string"}},
                        "required": ["source"], "additionalProperties": False,
                    }},
                    "rationale": {"type": "string"},
                }, "required": ["trigger_id", "intervention_type"], "additionalProperties": False,
            }
        }, "required": ["intervention_proposal"], "additionalProperties": False,
    }

    result = _call_llm(_SYSTEM, user, schema, provider=provider)
    proposal = result.get("intervention_proposal", {})
    cells = [NotebookCell(source=c.get("source", ""), role=c.get("role", "execute"), title=c.get("title", "")) for c in proposal.get("notebook_cells", [])]
    return InterventionProposal(
        proposal_id=f"prop_{uuid4().hex[:12]}",
        trigger_id=proposal.get("trigger_id", ""),
        intervention_type=proposal.get("intervention_type", "ask_user"),
        target_ids=proposal.get("target_ids", []),
        notebook_cells=cells,
        rationale=proposal.get("rationale", ""),
    )


# ── Critic ──────────────────────────────────────────────────────────────

_CRITIC_SYSTEM = """You are a scientific critic reviewing analysis outcomes. Detect:
- errors (code failed), artifact problems (empty/bad output)
- suspicious choices (wrong method/contrast), weak results (negative/unproductive)
- unsupported interpretations, missing context.
Prefer autonomous recovery (rerun, change_params, try_tool) over asking the user.
Ask user only for: control identity, permissions, exhausted budget."""


def review_outcome(outcome_summary: str, metrics: dict, ctx: Context, *, provider: str = "openai") -> list[dict]:
    user = json.dumps({
        "outcome": {"summary": outcome_summary, "status": metrics.get("status", ""), "metrics": metrics},
        "context_memory": [{"subject": m.subject, "signal": m.signal, "summary": m.summary} for m in ctx.memory[:6]],
    }, ensure_ascii=False, indent=2)[:6000]

    schema = {
        "type": "object", "properties": {
            "findings": {"type": "array", "items": {
                "type": "object", "properties": {
                    "finding_type": {"type": "string", "enum": ["continue_ok", "error", "artifact_problem", "suspicious_choice", "weak_result", "unsupported_interpretation", "missing_context"]},
                    "severity": {"type": "string", "enum": ["info", "warning", "blocking"]},
                    "suggested_action": {"type": "string", "enum": ["continue", "rerun", "change_params", "try_tool", "trace_upstream", "downgrade", "ask_user"]},
                    "summary": {"type": "string"},
                }, "required": ["finding_type", "severity", "summary"], "additionalProperties": False,
            }},
        }, "required": ["findings"], "additionalProperties": False,
    }

    result = _call_llm(_CRITIC_SYSTEM, user, schema, provider=provider)
    return result.get("findings", [])


def _serialize_ctx(ctx: Context) -> dict:
    return {
        "phase": ctx.phase, "goal": ctx.goal, "active_stage": ctx.active_stage,
        "attempts_done": ctx.attempts_done, "budget": ctx.budget_remaining,
        "open_triggers": ctx.open_triggers, "capabilities": ctx.capabilities,
        "memory": [{"subject": m.subject, "metric": m.metric, "signal": m.signal, "summary": m.summary, "current": m.current_value} for m in ctx.memory],
        "coverage": [{"subject": c.subject, "label": c.label, "methods": c.methods, "contradictions": c.contradictions} for c in ctx.coverage],
        "recent_findings": ctx.recent_findings,
    }
