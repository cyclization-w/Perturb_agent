"""SQLite event store + replay reducer."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from blackboard._schemas import (
    Event, Snapshot, Budget, Attempt, Outcome, Artifact, Observation,
    ReviewTrigger, Finding, Branch, Goal, Conclusion,
    Intervention, Interrupt, _model_dump,
)


class Store:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = run_dir / "events.db"
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE, event_type TEXT, run_id TEXT,
                    timestamp TEXT, actor TEXT, payload TEXT
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT, updated TEXT
                );
                CREATE TABLE IF NOT EXISTS graph (
                    id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT, updated TEXT
                );
            """)

    def append(self, events: list[Event]) -> list[Event]:
        current = self.read_events()
        all_events = current + events
        snap = reduce(all_events)
        graph = build_graph(snap)
        with sqlite3.connect(self.db_path) as conn:
            for e in events:
                conn.execute(
                    "INSERT INTO events(event_id,event_type,run_id,timestamp,actor,payload) VALUES(?,?,?,?,?,?)",
                    (e.event_id, e.event_type, e.run_id, e.timestamp.isoformat(), e.actor, json.dumps(e.payload, ensure_ascii=False)),
                )
            conn.execute(
                "INSERT OR REPLACE INTO snapshots(id,payload,updated) VALUES(1,?,?)",
                (json.dumps(_model_dump(snap), ensure_ascii=False, default=str), snap.run_id),
            )
            conn.execute(
                "INSERT OR REPLACE INTO graph(id,payload,updated) VALUES(1,?,?)",
                (json.dumps(_model_dump(graph), ensure_ascii=False, default=str), snap.run_id),
            )
        return events

    def read_events(self) -> list[Event]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY seq").fetchall()
        events = []
        for r in rows:
            p = json.loads(r[6])
            events.append(Event(event_id=r[1], event_type=r[2], run_id=r[3], timestamp=r[4], actor=r[5], payload=p))
        return events

    def read_snapshot(self) -> Snapshot | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM snapshots WHERE id=1").fetchone()
        if row:
            return Snapshot(**json.loads(row[0]))
        return None

    def read_graph(self) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM graph WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    # ── JSONL export ──────────────────────────────────────────────────

    def export_jsonl(self) -> Path:
        events = self.read_events()
        path = self.run_dir / "events.jsonl"
        lines = [json.dumps(_model_dump(e), ensure_ascii=False, default=str) for e in events]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    # ── Lease management ──────────────────────────────────────────────

    def acquire_lease(self, owner: str, ttl_seconds: int = 900) -> bool:
        from datetime import datetime, timedelta, timezone
        lease_path = self.run_dir / ".lease"
        now = datetime.now(timezone.utc)
        if lease_path.exists():
            try:
                data = json.loads(lease_path.read_text())
                expires = datetime.fromisoformat(data["expires"])
                if expires > now and data["owner"] != owner:
                    return False
            except Exception:
                pass
        lease_path.write_text(json.dumps({"owner": owner, "expires": (now + timedelta(seconds=ttl_seconds)).isoformat(), "acquired": now.isoformat()}))
        return True

    def release_lease(self, owner: str):
        lease_path = self.run_dir / ".lease"
        if lease_path.exists():
            try:
                data = json.loads(lease_path.read_text())
                if data.get("owner") == owner:
                    lease_path.unlink()
            except Exception:
                pass


# ── Event reducer ───────────────────────────────────────────────────────

def reduce(events: list[Event]) -> Snapshot:
    if not events:
        raise ValueError("Empty event log")
    first = events[0]
    cfg = first.payload.get("config", {})
    snap = Snapshot(
        run_id=cfg.get("run_id", ""), workspace=cfg.get("workspace", ""),
        goal=cfg.get("goal", ""), domain=cfg.get("domain", ""),
        protocol=cfg.get("protocol", ""),
        budget=Budget(**cfg.get("budget", {})),
        branches=[Branch(branch_id="main", title="Main", reason="main")],
        capabilities=cfg.get("capabilities", []),
    )
    for e in events:
        _apply(snap, e)
    return snap


def _apply(snap: Snapshot, e: Event):
    p = e.payload
    if e.event_type == "run_started":
        snap.phase = "planning"
    elif e.event_type == "attempt_planned":
        a = Attempt(**p["attempt"])
        _upsert(snap.attempts, "attempt_id", a)
        snap.active_attempt = a.attempt_id
    elif e.event_type == "outcome_recorded":
        o = Outcome(**p["outcome"])
        _upsert(snap.outcomes, "outcome_id", o)
        for a in snap.attempts:
            if a.attempt_id == o.attempt_id:
                a.status = "failed" if o.status == "error" else "succeeded" if o.status == "success" else o.status
                break
        snap.phase = "reviewing"
    elif e.event_type == "artifact_registered":
        _upsert(snap.artifacts, "artifact_id", Artifact(**p["artifact"]))
    elif e.event_type == "observation_registered":
        _upsert(snap.observations, "observation_id", Observation(**p["observation"]))
    elif e.event_type == "trigger_opened":
        _upsert(snap.triggers, "trigger_id", ReviewTrigger(**p["trigger"]))
        snap.phase = "diagnosing"
    elif e.event_type == "trigger_resolved":
        for t in snap.triggers:
            if t.trigger_id == p.get("trigger_id"):
                t.status = "resolved"
                break
        snap.phase = "planning"
    elif e.event_type == "finding_recorded":
        _upsert(snap.findings, "finding_id", Finding(**p["finding"]))
    elif e.event_type == "branch_opened":
        _upsert(snap.branches, "branch_id", Branch(**p["branch"]))
        snap.active_branch = p["branch"]["branch_id"]
    elif e.event_type == "branch_stopped":
        for b in snap.branches:
            if b.branch_id == p.get("branch_id"):
                b.status = "stopped"; break
    elif e.event_type == "goal_recorded":
        _upsert(snap.goals, "goal_id", Goal(**p["goal"]))
    elif e.event_type == "conclusion_recorded":
        _upsert(snap.conclusions, "conclusion_id", Conclusion(**p["conclusion"]))
    elif e.event_type == "intervention_planned":
        _upsert(snap.interventions, "intervention_id", Intervention(**p["intervention"]))
        snap.phase = "planning_intervention"
    elif e.event_type == "intervention_applied":
        for i in snap.interventions:
            if i.intervention_id == p.get("intervention_id"):
                i.status = "applied"; break
    elif e.event_type == "interrupt_opened":
        _upsert(snap.interrupts, "interrupt_id", Interrupt(**p["interrupt"]))
        snap.phase = "waiting_for_human"
    elif e.event_type == "interrupt_resolved":
        for i in snap.interrupts:
            if i.interrupt_id == p.get("interrupt_id"):
                i.status = "resolved"; break
        snap.phase = "planning"
    elif e.event_type == "run_paused":
        snap.phase = "paused"
    elif e.event_type == "run_resumed":
        snap.phase = "planning"
    elif e.event_type == "run_complete":
        snap.phase = "complete"


def _upsert(items, key, value):
    for i, item in enumerate(items):
        if getattr(item, key) == getattr(value, key):
            items[i] = value; return
    items.append(value)


# ── Graph derivation ────────────────────────────────────────────────────

def build_graph(snap: Snapshot) -> dict:
    nodes, edges = [], []

    def n(id, type, label, summary="", status="", meta=None):
        nodes.append({"node_id": id, "node_type": type, "label": label, "summary": summary, "status": status, "metadata": meta or {}})

    def e(src, tgt, etype):
        if src and tgt:
            edges.append({"source_id": src, "target_id": tgt, "edge_type": etype})

    n("root", "workspace", snap.workspace or "Workspace", status=snap.phase)
    for b in snap.branches:
        n(b.branch_id, "branch", b.title, status=b.status); e("root", b.branch_id, "contains")
        if b.parent_id:
            e(b.parent_id, b.branch_id, "branches_from")
    for a in snap.attempts:
        n(a.attempt_id, "attempt", a.title, summary=a.objective, status=a.status, meta={"stage": a.stage, "parameters": a.parameters})
        e(a.branch_id, a.attempt_id, "contains")
        for pid in a.parent_ids:
            e(pid, a.attempt_id, "depends_on")
    for o in snap.outcomes:
        n(o.outcome_id, "outcome", o.status, summary=o.summary, status=o.status)
        e(o.attempt_id, o.outcome_id, "summarizes")
    for a in snap.artifacts:
        n(a.artifact_id, "artifact", a.kind, summary=a.summary)
        if a.attempt_id:
            e(a.attempt_id, a.artifact_id, "produces")
    for obs in snap.observations:
        n(obs.observation_id, "observation", f"{obs.type}:{obs.target}", summary=f"{obs.metric}={obs.value}", status=obs.type)
        e(obs.attempt_id, obs.observation_id, "produces")
        if obs.artifact_id:
            e(obs.artifact_id, obs.observation_id, "observes")
    for t in snap.triggers:
        n(t.trigger_id, "trigger", t.trigger_type, summary=t.summary, status=t.status)
        if t.attempt_id:
            e(t.attempt_id, t.trigger_id, "triggers")
    for c in snap.conclusions:
        n(c.conclusion_id, "conclusion", c.grade, summary=c.text)
        for sid in c.support_ids:
            e(sid, c.conclusion_id, "supports")
        for lid in c.limitation_ids:
            e(lid, c.conclusion_id, "limits")

    return {"run_id": snap.run_id, "nodes": _dedupe(nodes, "node_id"), "edges": _dedupe(edges, None, key_fn=lambda e: f"{e['source_id']}|{e['target_id']}|{e['edge_type']}")}


# ── Graph validation ────────────────────────────────────────────────────

_ALLOWED_EDGES = {
    "contains": set(), "depends_on": set(), "informs": set(), "supersedes": set(),
    "produces": {("attempt", "artifact"), ("attempt", "observation")},
    "summarizes": {("attempt", "outcome")},
    "triggers": {("attempt", "trigger"), ("outcome", "trigger")},
    "branches_from": {("branch", "branch")},
    "supports": {("observation", "conclusion"), ("artifact", "conclusion")},
    "limits": {("finding", "conclusion"), ("trigger", "conclusion")},
    "observes": {("artifact", "observation")},
}


def validate_graph(graph: dict) -> list[str]:
    """Validate edge type / node type consistency. Returns list of violations."""
    violations = []
    node_types = {n["node_id"]: n["node_type"] for n in graph.get("nodes", [])}
    for edge in graph.get("edges", []):
        src = edge.get("source_id", "")
        tgt = edge.get("target_id", "")
        etype = edge.get("edge_type", "")
        if src not in node_types:
            violations.append(f"Edge source not found: {src}")
        if tgt not in node_types:
            violations.append(f"Edge target not found: {tgt}")
        if etype in _ALLOWED_EDGES and _ALLOWED_EDGES[etype]:
            stype = node_types.get(src, "")
            ttype = node_types.get(tgt, "")
            if (stype, ttype) not in _ALLOWED_EDGES[etype]:
                violations.append(f"Illegal edge: {stype} -[{etype}]-> {ttype}")
    return violations


# ── Incremental replay ──────────────────────────────────────────────────

def reduce_incremental(snap: Snapshot, new_events: list[Event]) -> Snapshot:
    """Apply only new events to an existing snapshot, avoiding full replay."""
    for e in new_events:
        _apply(snap, e)
    return snap


def _dedupe(items, key_field=None, *, key_fn=None):
    seen = set()
    result = []
    for item in items:
        k = key_fn(item) if key_fn else item[key_field] if key_field else str(item)
        if k not in seen:
            seen.add(k); result.append(item)
    return result
