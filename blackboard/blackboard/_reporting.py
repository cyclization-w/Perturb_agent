"""Report generation — synthesis, Markdown, and derivation graph HTML."""

from __future__ import annotations

import json
from datetime import datetime, timezone


def generate_report(snap, ctx, *, provider: str = "openai") -> dict:
    """Synthesize a complete analysis report."""
    report = {
        "run_id": snap.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace": snap.workspace,
        "goal": snap.goal,
        "domain": snap.domain,
        "summary": {
            "attempts_total": len(snap.attempts),
            "attempts_succeeded": len([a for a in snap.attempts if a.status == "succeeded"]),
            "observations_total": len(snap.observations),
            "conclusions": _summary_conclusions(snap),
            "branches": len(snap.branches),
        },
        "findings": _findings_summary(snap),
        "coverage": _coverage_summary(ctx),
        "memory_signals": _memory_summary(ctx),
        "observation_detail": _observation_detail(snap),
        "narrative": _generate_narrative(snap, ctx, provider),
    }
    return report


def render_markdown(report: dict, graph_html_path: str = "") -> str:
    """Render the report as Markdown."""
    s = report["summary"]
    md = f"# Analysis Report — {report['run_id']}\n\n"
    md += f"**Workspace:** `{report['workspace']}`  \n"
    md += f"**Goal:** {report['goal'] or 'N/A'}  \n"
    md += f"**Domain:** {report['domain']}  \n"
    md += f"**Generated:** {report['generated_at']}\n\n"

    md += "## Summary\n\n"
    md += f"| Metric | Value |\n|---|---|\n"
    md += f"| Attempts | {s['attempts_total']} ({s['attempts_succeeded']} succeeded) |\n"
    md += f"| Observations | {s['observations_total']} |\n"
    md += f"| Branches | {s['branches']} |\n\n"

    for c in s["conclusions"]:
        md += f"**{c['grade'].upper()}**: {c['text']}\n\n"

    if report["narrative"]:
        md += "## Narrative\n\n" + report["narrative"] + "\n\n"

    if report["findings"]:
        md += "## Key Findings\n\n"
        for f in report["findings"]:
            md += f"- [{f['severity']}] {f['summary']}\n"
        md += "\n"

    if report["coverage"]:
        md += "## Evidence Coverage\n\n"
        md += "| Subject | Label | Methods | Observations | Contradictions |\n|---|---|---|---|---|\n"
        for c in report["coverage"]:
            md += f"| {c['subject']} | {c['label']} | {c['methods']} | {c['observations']} | {c['contradictions']} |\n"
        md += "\n"

    if report["memory_signals"]:
        md += "## Memory Signals\n\n"
        for m in report["memory_signals"]:
            md += f"- **{m['signal']}**: {m['subject']} — {m['summary']}\n"
        md += "\n"

    if report["observation_detail"]:
        md += "## Observation Details\n\n"
        md += "| Target | Type | Metric | Value | Method | Contrast |\n|---|---|---|---|---|---|\n"
        for o in report["observation_detail"][:30]:
            md += f"| {o['target']} | {o['type']} | {o['metric']} | {o['value']} | {o['method']} | {o['contrast']} |\n"
        md += "\n"

    if graph_html_path:
        md += f"## Derivation Graph\n\n[Open interactive graph]({graph_html_path})\n\n"

    return md


def render_html(report: dict, graph_json: dict | None = None) -> str:
    """Render the report as a self-contained HTML page with embedded graph."""
    md = render_markdown(report)
    graph_js = json.dumps(graph_json or {}, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Report — {report['run_id']}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.0/cytoscape.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/dagre/0.8.5/dagre.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:Inter,system-ui,sans-serif;background:#0f172a;color:#e2e8f0;max-width:960px;margin:0 auto;padding:24px}}
h1{{font-size:24px;margin-bottom:8px}}h2{{font-size:18px;margin:24px 0 8px;color:#94a3b8}}
p,li{{font-size:14px;line-height:1.6}}table{{width:100%;border-collapse:collapse;margin:8px 0;font-size:13px}}
th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid#334155}}
th{{color:#94a3b8;font-weight:600}}
#cy{{width:100%;height:400px;border:1px solid#334155;border-radius:8px;margin:16px 0}}
.badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600}}
</style>
</head>
<body>
<h1>Analysis Report</h1>
<p><strong>Run:</strong> {report['run_id']} &nbsp; <strong>Workspace:</strong> {report['workspace']} &nbsp; <strong>Goal:</strong> {report['goal']}</p>

<h2>Summary</h2>
<table><tr><th>Metric</th><th>Value</th></tr>
<tr><td>Attempts</td><td>{report['summary']['attempts_total']} ({report['summary']['attempts_succeeded']} succeeded)</td></tr>
<tr><td>Observations</td><td>{report['summary']['observations_total']}</td></tr>
<tr><td>Branches</td><td>{report['summary']['branches']}</td></tr></table>

{"<h2>Derivation Graph</h2><div id='cy'></div>" if graph_json else ""}

<h2>Narrative</h2>
<p>{report.get('narrative', 'No narrative generated.')}</p>

<h2>Coverage</h2>
<table><tr><th>Subject</th><th>Label</th><th>Methods</th><th>Observations</th><th>Contradictions</th></tr>
{"".join(f"<tr><td>{c['subject']}</td><td><span class='badge' style='background:{_coverage_color(c['label'])}'>{c['label']}</span></td><td>{c['methods']}</td><td>{c['observations']}</td><td>{c['contradictions']}</td></tr>" for c in report.get('coverage', []))}</table>

<h2>Memory Signals</h2>
{"".join(f"<p><strong>{m['signal'].upper()}</strong>: {m['subject']} — {m['summary']}</p>" for m in report.get('memory_signals', [])) or '<p>No memory signals.</p>'}

<h2>Observation Details</h2>
<table><tr><th>Target</th><th>Type</th><th>Metric</th><th>Value</th><th>Method</th><th>Contrast</th></tr>
{"".join(f"<tr><td>{o['target']}</td><td>{o['type']}</td><td>{o['metric']}</td><td>{o['value']}</td><td>{o['method']}</td><td>{o['contrast']}</td></tr>" for o in report.get('observation_detail', [])[:30])}</table>

<script>
const graphData = {graph_js};
if(graphData.nodes && graphData.nodes.length){{
  const cy=cytoscape({{container:document.getElementById('cy'),style:[
    {{selector:'node',style:{{label:'data(label)',color:'#e2e8f0','font-size':'9px','text-valign':'bottom','text-halign':'center','text-margin-y':3,'border-width':2,'background-opacity':0.85}}}},
    {{selector:'edge',style:{{width:1,'line-color':'#475569','target-arrow-color':'#475569','target-arrow-shape':'triangle','curve-style':'bezier','arrow-scale':0.7,opacity:0.5}}}},
  ],layout:{{name:'dagre',rankDir:'LR',spacingFactor:1.2}},elements:graphData}});
}}
</script>
</body></html>"""


def _coverage_color(label: str) -> str:
    return {"convergent": "#22c55e", "adequate": "#3b82f6", "thin": "#f59e0b", "conflicted": "#ef4444", "none": "#64748b"}.get(label, "#64748b")


def _summary_conclusions(snap) -> list[dict]:
    return [{"grade": c.grade, "text": c.text} for c in snap.conclusions]


def _findings_summary(snap) -> list[dict]:
    return [
        {"severity": f.severity, "type": f.finding_type, "summary": f.summary, "action": f.suggested_action}
        for f in snap.findings[-20:]
    ]


def _coverage_summary(ctx) -> list[dict]:
    return [
        {"subject": c.subject, "label": c.label, "methods": c.methods,
         "observations": c.observations, "contradictions": c.contradictions}
        for c in (ctx.coverage if ctx else [])
    ]


def _memory_summary(ctx) -> list[dict]:
    return [
        {"signal": m.signal, "subject": m.subject, "summary": m.summary, "value": m.current_value}
        for m in (ctx.memory if ctx else [])
    ]


def _observation_detail(snap) -> list[dict]:
    return [
        {"target": o.target, "type": o.type, "metric": o.metric, "value": o.value,
         "method": o.method, "contrast": o.contrast}
        for o in snap.observations[-50:]
    ]


def _generate_narrative(snap, ctx, provider: str) -> str:
    """Use LLM to synthesize a narrative from the evidence."""
    from blackboard._planner import _call_llm, _api_key, _anthropic_key
    has_key = _api_key() if provider == "openai" else _anthropic_key()
    if not has_key:
        return "Narrative requires an LLM API key."

    coverage = _coverage_summary(ctx)
    memory = _memory_summary(ctx)
    obs = _observation_detail(snap)[:20]
    findings = _findings_summary(snap)[:10]
    conclusions = _summary_conclusions(snap)

    prompt = json.dumps({
        "goal": snap.goal,
        "domain": snap.domain,
        "attempts": len(snap.attempts),
        "observations": obs,
        "coverage": coverage,
        "memory_signals": memory,
        "findings": findings,
        "conclusions": conclusions,
    }, ensure_ascii=False, default=str)[:8000]

    system = "You are a scientific writer. Write a concise narrative summary (3-5 paragraphs) of the analysis. Include: what was done, key findings, evidence quality, limitations, and recommendations."

    try:
        result = _call_llm(system, prompt, {
            "type": "object", "properties": {"narrative": {"type": "string"}},
            "required": ["narrative"], "additionalProperties": False,
        }, provider=provider)
        return result.get("narrative", "")
    except Exception:
        return "Narrative generation failed."
