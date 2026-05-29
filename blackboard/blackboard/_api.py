"""FastAPI server for the blackboard workbench."""

from __future__ import annotations

import json
from pathlib import Path


def create_app(workbench):
    from pathlib import Path as _Path
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel
    from blackboard._schemas import _model_dump
    from blackboard._store import Store as _Store
    from blackboard._jobs import JobRunner

    def _open_store(run_id: str):
        d = _Path("runs") / run_id
        return _Store(d) if (d / "events.db").exists() else None

    app = FastAPI(title="Blackboard Workbench", version="1.0.0")
    runner = JobRunner()

    class RunRequest(BaseModel):
        workspace: str
        goal: str = ""
        steps: int = 5

    class AnswerRequest(BaseModel):
        answer: str

    @app.get("/", response_class=HTMLResponse)
    def gui():
        return _GUI_HTML.replace("{domain_name}", workbench.domain.name)

    @app.get("/api/status")
    def status():
        return workbench.status

    @app.get("/api/graph")
    def graph():
        return workbench.graph or {"nodes": [], "edges": []}

    @app.get("/api/report")
    def report():
        return workbench.report()

    @app.post("/api/run")
    def run(req: RunRequest):
        result = workbench.run(req.workspace, goal=req.goal, steps=req.steps)
        return {**result, **workbench.status}

    @app.post("/api/step")
    def step():
        actions = workbench.step(1)
        return {"actions": actions, **workbench.status}

    # ── Run browsing ──────────────────────────────────────────────────

    @app.get("/api/runs")
    def list_runs():
        runs_dir = Path("runs")
        if not runs_dir.exists():
            return {"runs": []}
        runs = []
        for d in sorted(runs_dir.iterdir(), reverse=True):
            db = d / "events.db"
            if not db.exists():
                continue
            try:
                from blackboard._store import Store
                s = Store(d)
                snap = s.read_snapshot()
                runs.append({
                    "run_id": snap.run_id if snap else d.name,
                    "phase": snap.phase if snap else "unknown",
                    "workspace": snap.workspace if snap else "",
                    "goal": snap.goal if snap else "",
                    "attempts": len(snap.attempts) if snap else 0,
                    "observations": len(snap.observations) if snap else 0,
                })
            except Exception:
                runs.append({"run_id": d.name, "phase": "error"})
        return {"runs": runs}

    # ── Artifact preview ──────────────────────────────────────────────

    @app.get("/api/artifacts")
    def list_artifacts(run_id: str = ""):
        rid = run_id or workbench._run_id
        store = _open_store(rid) if rid != workbench._run_id else workbench._store
        if not store:
            return {"artifacts": []}
        snap = store.read_snapshot()
        if not snap:
            return {"artifacts": []}
        return {"artifacts": [_model_dump(a) for a in snap.artifacts]}

    @app.get("/api/artifacts/{artifact_id}/preview")
    def preview_artifact(artifact_id: str):
        snap = workbench._store.read_snapshot() if workbench._store else None
        if not snap:
            from fastapi import HTTPException
            raise HTTPException(404, "No data")
        for a in snap.artifacts:
            if a.artifact_id == artifact_id:
                path = Path(a.path)
                if path.exists() and path.is_file():
                    try:
                        content = path.read_text(encoding="utf-8")[:5000]
                        return {"artifact_id": artifact_id, "path": a.path, "kind": a.kind, "preview": content, "size": path.stat().st_size}
                    except Exception:
                        return {"artifact_id": artifact_id, "path": a.path, "kind": a.kind, "preview": "[binary or unreadable]", "size": path.stat().st_size if path.exists() else 0}
                return {"artifact_id": artifact_id, "path": a.path, "kind": a.kind, "preview": "[file not found]"}
        from fastapi import HTTPException
        raise HTTPException(404, f"Artifact {artifact_id} not found")

    # ── Async jobs ──────────────────────────────────────────────────

    @app.post("/api/jobs/run")
    def start_run_job(req: RunRequest):
        def _run(cancel_event):
            return workbench.run(req.workspace, goal=req.goal, steps=req.steps)
        job = runner.submit(_run)
        return {"job_id": job.job_id, "status": "queued"}

    @app.post("/api/jobs/step")
    def start_step_job():
        def _step(cancel_event):
            return {"actions": workbench.step(1)}
        job = runner.submit(_step)
        return {"job_id": job.job_id, "status": "queued"}

    @app.get("/api/jobs")
    def list_jobs():
        return {"jobs": runner.list_jobs()}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = runner.get(job_id)
        if not job:
            from fastapi import HTTPException
            raise HTTPException(404, f"Job {job_id} not found")
        return job.to_dict()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        ok = runner.cancel(job_id)
        return {"cancelled": ok}

    # ── Interrupts ──────────────────────────────────────────────────

    @app.post("/api/answer/{interrupt_id}")
    def answer(interrupt_id: str, req: AnswerRequest):
        workbench.answer(interrupt_id, req.answer)
        return workbench.status

    @app.get("/api/interrupts")
    def interrupts():
        snap = workbench._store.read_snapshot() if workbench._store else None
        return {"interrupts": [_model_dump(i) for i in (snap.interrupts if snap else []) if i.status == "open"]}

    return app


_GUI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Blackboard — {domain_name}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.0/cytoscape.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/dagre/0.8.5/dagre.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Inter,system-ui,sans-serif;background:#0f172a;color:#e2e8f0;height:100vh;display:flex;flex-direction:column}
header{display:flex;align-items:center;justify-content:space-between;padding:8px 16px;border-bottom:1px solid #334155;background:#1e293b}
header h1{font-size:16px;font-weight:700}.btn{padding:6px 14px;border-radius:6px;font-size:13px;border:none;cursor:pointer;font-weight:500}
.btn-primary{background:#3b82f6;color:#fff}.btn-primary:hover{background:#2563eb}
.btn-success{background:#22c55e;color:#000}.btn-danger{background:#ef4444;color:#fff}
.btn-ghost{background:transparent;color:#94a3b8;border:1px solid #475569}
.input{background:#0f172a;border:1px solid#475569;border-radius:6px;padding:6px 10px;color:#e2e8f0;font-size:13px}
.input:focus{outline:none;border-color:#3b82f6}
main{flex:1;display:flex;overflow:hidden}
#cy{flex:1;background:#0f172a}
aside{width:280px;border-left:1px solid #334155;background:#1e293b;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:8px}
.panel{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px}
.panel-title{font-size:10px;font-weight:600;color:#64748b;text-transform:uppercase;margin-bottom:6px}
.mem{font-size:11px;padding:4px 8px;border-radius:4px;margin:2px 0;border-left:3px solid}
.mem.conflict{background:#7f1d1d33;border-color:#ef4444}
.mem.warning{background:#78350f33;border-color:#f59e0b}
.mem.agreement{background:#064e3b33;border-color:#22c55e}
.mem.thin{background:#1e3a5f33;border-color:#3b82f6}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600}
.status-dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:4px}
.status-dot.running{background:#3b82f6;animation:pulse 1.5s infinite}
.status-dot.waiting{background:#f59e0b}.status-dot.complete{background:#22c55e}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}
</style>
</head>
<body>
<header>
  <h1>Blackboard — {domain_name}</h1>
  <div style="display:flex;gap:8px;align-items:center">
    <input id="workspace" class="input" placeholder="Workspace path..." style="width:200px">
    <input id="goal" class="input" placeholder="Goal (optional)..." style="width:200px">
    <button class="btn btn-primary" onclick="initRun()">Init</button>
    <button class="btn btn-primary" onclick="step()">Step</button>
    <button class="btn btn-success" onclick="run(5)">Run 5</button>
    <span id="phase" style="font-size:12px;color:#94a3b8"></span>
  </div>
</header>
<main>
  <div id="cy"></div>
  <aside>
    <div class="panel"><div class="panel-title">Status</div><div id="status" style="font-size:11px;color:#94a3b8">Not initialized</div></div>
    <div class="panel"><div class="panel-title">Memory</div><div id="memory" style="font-size:11px;color:#94a3b8">—</div></div>
    <div class="panel"><div class="panel-title">Coverage</div><div id="coverage" style="font-size:11px;color:#94a3b8">—</div></div>
    <div class="panel"><div class="panel-title">Triggers</div><div id="triggers" style="font-size:11px;color:#94a3b8">—</div></div>
    <div class="panel" id="interrupt-panel" style="display:none;border-color:#dc2626"><div class="panel-title" style="color:#ef4444">INTERRUPT</div><div id="interrupt-text" style="font-size:11px;margin-bottom:4px"></div><input id="answer-input" class="input" placeholder="Your answer..." style="font-size:11px"><button class="btn btn-primary" style="margin-top:4px;width:100%;font-size:11px" onclick="answer()">Send</button></div>
    <div class="panel" id="conclusions-panel"><div class="panel-title">CONCLUSIONS</div><div id="conclusions-text" style="font-size:11px;color:#94a3b8">—</div></div>
  </aside>
</main>
<script>
let cy=null
const COLORS={attempt:'#22c55e',artifact:'#f59e0b',observation:'#14b8a6',outcome:'#10b981',trigger:'#ef4444',branch:'#3b82f6',conclusion:'#eab308',finding:'#f43f5e',workspace:'#64748b'}

function initGraph(){
  if(cy)cy.destroy()
  cy=cytoscape({container:document.getElementById('cy'),style:[
    {selector:'node',style:{label:'data(label)',color:'#e2e8f0','font-size':'9px','text-valign':'bottom','text-halign':'center','text-margin-y':3,'border-width':2,'background-opacity':0.85,width:'mapData(size,5,50,22,48)',height:'mapData(size,5,50,22,48)'}},
    {selector:'edge',style:{width:1,'line-color':'#475569','target-arrow-color':'#475569','target-arrow-shape':'triangle','curve-style':'bezier','arrow-scale':0.7,opacity:0.5}},
    {selector:'node:selected',style:{'border-color':'#fff','border-width':3}},
  ],layout:{name:'dagre',rankDir:'LR',spacingFactor:1.2}})
  cy.on('tap','node',e=>{const d=e.target.data();document.getElementById('status').innerHTML=`<b>${d.label}</b><br><span class="badge" style="background:${COLORS[d.node_type]||'#64748b'}">${d.node_type}</span> ${d.status||''}<br><small>${(d.summary||'').slice(0,200)}</small>`})
}
async function refresh(){
  try{
    const g=await(await fetch('/api/graph')).json()
    if(!cy)initGraph()
    const oldIds=new Set(cy.nodes().map(n=>n.id()))
    const newIds=new Set()
    const nodes=(g.nodes||[]).map(n=>{newIds.add(n.node_id);return{group:'nodes',data:{id:n.node_id,label:(n.label||'').slice(0,30),node_type:n.node_type,summary:n.summary||'',status:n.status||'',size:n.node_type==='attempt'?30:n.node_type==='observation'?25:20},style:{'background-color':COLORS[n.node_type]||'#64748b',shape:n.node_type==='observation'?'diamond':n.node_type==='conclusion'?'star':'ellipse'}}})
    const edges=(g.edges||[]).map(e=>{newIds.add(e.source_id);newIds.add(e.target_id);return{group:'edges',data:{id:`${e.source_id}|${e.target_id}|${e.edge_type}`,source:e.source_id,target:e.target_id}}})

    cy.nodes().forEach(n=>{if(!newIds.has(n.id()))n.remove()})
    const toAdd= nodes.filter(n=>!oldIds.has(n.data.id))
    cy.add([...toAdd,...edges.filter(e=>{const k=`${e.data.source}|${e.data.target}|${e.data.edge_type}`;return !oldIds.has(k)})])
    if(toAdd.length)cy.layout({name:'dagre',rankDir:'LR',spacingFactor:1.2}).run()
    cy.nodes().forEach(n=>{const t=n.data('node_type');n.style('background-color',COLORS[t]||'#64748b')})
  }catch(e){}
  try{
    const r=await(await fetch('/api/report')).json()
    document.getElementById('status').innerHTML=`<b>${r.status?.phase||'?'}</b><br>${r.status?.attempts||0} attempts · ${r.status?.observations||0} obs<br>${r.status?.workspace||''}`
    document.getElementById('phase').textContent=r.status?.phase||''
    document.getElementById('memory').innerHTML=(r.memory||[]).slice(0,8).map(m=>`<div class="mem ${m.signal}">${m.subject} <span style="color:#94a3b8">${m.signal}</span></div>`).join('')||'—'
    document.getElementById('coverage').innerHTML=(r.coverage||[]).slice(0,6).map(c=>`<div style="font-size:10px;margin:2px 0"><span>${c.subject}</span> <span style="color:#94a3b8">${c.label}</span></div>`).join('')||'—'
    const cons=r.conclusions||[];document.getElementById('conclusions-text').innerHTML=cons.length?cons.map(c=>`<div style="font-size:10px;margin:2px 0;padding:4px;background:#1e293b;border-radius:4px"><span class="badge" style="background:${c.grade==='robust'?'#22c55e':c.grade==='supported'?'#3b82f6':c.grade==='tentative'?'#f59e0b':'#64748b'}">${c.grade}</span> <span style="color:#e2e8f0">${(c.text||'').slice(0,150)}</span></div>`).join(''):'—'
  }catch(e){}
}
async function initRun(){const ws=document.getElementById('workspace').value;const goal=document.getElementById('goal').value;await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace:ws,goal:goal,steps:0})});refresh()}
async function step(){await fetch('/api/step',{method:'POST'});refresh()}
async function run(n){const ws=document.getElementById('workspace').value||'data';const goal=document.getElementById('goal').value;await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace:ws,goal:goal,steps:n})});refresh()}
async function answer(){const irqs=await(await fetch('/api/interrupts')).json();const open=irqs.interrupts||[];if(open.length){const a=document.getElementById('answer-input').value;await fetch('/api/answer/'+open[0].interrupt_id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answer:a})});refresh();document.getElementById('answer-input').value=''}}
async function loadInterrupts(){try{const r=await(await fetch('/api/interrupts')).json();const open=r.interrupts||[];const panel=document.getElementById('interrupt-panel');if(open.length){panel.style.display='block';document.getElementById('interrupt-text').textContent=open[0].question||'Pending response'}else{panel.style.display='none'}}catch(e){}}
async function refresh(){
  loadInterrupts()
  try{
</script>
</body>
</html>"""
