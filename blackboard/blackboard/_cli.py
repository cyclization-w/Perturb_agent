"""CLI — single-command startup."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from blackboard._domain import Domain
from blackboard._workbench import Workbench


def main():
    p = argparse.ArgumentParser(prog="blackboard", description="LLM-driven analysis with provenance memory.")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="Run analysis (non-interactive).")
    r.add_argument("workspace", help="Data directory.")
    r.add_argument("--domain", default="perturbseq", help="Domain name or path to domain.json.")
    r.add_argument("--goal", default="", help="Analysis goal.")
    r.add_argument("--steps", type=int, default=5)
    r.add_argument("--provider", choices=["openai", "anthropic"], default="openai")
    r.add_argument("--model", default=None)
    r.add_argument("--base-url", default=None)
    r.add_argument("--sandbox", choices=["subprocess", "docker"], default="subprocess")

    c = sub.add_parser("chat", help="Interactive analysis session.")
    c.add_argument("workspace", nargs="?", default=None, help="Data directory (optional).")
    c.add_argument("--domain", default="perturbseq")
    c.add_argument("--provider", choices=["openai", "anthropic"], default="openai")
    c.add_argument("--model", default=None)
    c.add_argument("--base-url", default=None)

    s = sub.add_parser("serve", help="Start GUI.")
    s.add_argument("--domain", default="perturbseq")
    s.add_argument("--port", type=int, default=8765)

    d = sub.add_parser("doctor", help="Check environment.")
    d.add_argument("--openai", action="store_true")

    args = p.parse_args()

    if args.cmd == "chat":
        return _chat(args)
    if args.cmd == "run":
        if args.model:
            os.environ["OPENAI_MODEL"] = args.model
        if args.base_url:
            os.environ["OPENAI_BASE_URL"] = args.base_url
        wb = Workbench(domain=_load_domain(args.domain), provider=args.provider, sandbox=args.sandbox)
        result = wb.run(args.workspace, goal=args.goal, steps=args.steps)
        print(json.dumps(result, indent=2))
        _print_run_summary(wb)
        return 0
    if args.cmd == "serve":
        wb = Workbench(domain=_load_domain(args.domain))
        wb.serve(args.port)
        return 0
    if args.cmd == "doctor":
        return _doctor(args)
    p.print_help()
    return 2


# ── Rich terminal setup ──────────────────────────────────────────────────

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box
    console = Console()
    RICH = True
except ImportError:
    RICH = False


def _print(*args, **kwargs):
    if RICH:
        for a in args:
            if isinstance(a, Panel):
                console.print(a, **kwargs)
            else:
                console.print(a, **kwargs)
    else:
        text = " ".join(str(a) for a in args)
        print(text)


def _panel(content, title="", style="", **kw):
    if RICH:
        return Panel(content, title=title, border_style=style, padding=(0, 1), **kw)
    return str(content)


# ── Chat ─────────────────────────────────────────────────────────────────

def _chat(args) -> int:
    import readline  # noqa
    from rich.live import Live
    from rich.layout import Layout
    from rich.spinner import Spinner

    if args.model:
        os.environ["OPENAI_MODEL"] = args.model
    if args.base_url:
        os.environ["OPENAI_BASE_URL"] = args.base_url

    wb = Workbench(domain=_load_domain(args.domain), provider=args.provider)
    workspace = args.workspace or "/tmp/blackboard_ws"

    # ── Startup banner ────────────────────────────────────────────────

    _print()
    ws_label = f"[bold white]{workspace}[/]" if args.workspace else f"[dim]{workspace} (no data yet)[/]"
    _print(_panel(
        f"  {ws_label}\n"
        f"  domain: [cyan]{args.domain}[/]  ·  model: [cyan]{args.provider}[/]\n"
        f"  [dim]Set workspace: [bold]ws /path/to/data[/]  ·  [bold]quit[/] to exit.[/]",
        title="⚙ Blackboard", style="bright_blue",
    ))
    _print("  [dim]Initializing…[/]", end="\r")

    wb.run(workspace, goal="", steps=0)  # init only

    snap = wb._store.read_snapshot()
    _print("  [green]Ready.[/] " + _status_line(snap))

    # ── Loop ──────────────────────────────────────────────────────────

    while True:
        try:
            if RICH:
                cmd = console.input("\n[bold bright_blue]▸[/] ").strip()
            else:
                cmd = input("\n▸ ").strip()
        except (EOFError, KeyboardInterrupt):
            _print()
            break

        if not cmd:
            continue
        if cmd.lower() in ("q", "quit", "exit"):
            _print(_panel("[dim]Shutting down…[/]", style="dim"))
            break

        if cmd.lower().startswith("ws "):
            new_ws = cmd[3:].strip()
            workspace = new_ws
            _print(_panel(f"Workspace → [bold]{new_ws}[/]", title="Config", style="dim"))
            wb.run(new_ws, goal="", steps=0)
            continue

        # ── Handle interrupt if open ──────────────────────────────────
        snapshot = wb._store.read_snapshot()
        open_intr = next((i for i in snapshot.interrupts if i.status == "open"), None)

        if open_intr:
            _print(_panel(
                f"[bold bright_yellow]Answered:[/] {cmd}",
                title="⚡ Interrupt Resolved", style="bright_yellow",
            ))
            wb.answer(open_intr.interrupt_id, cmd)
            action = _run_with_progress(wb)
            if action != "error":
                _print(*_step_result(action, wb))
            continue

        # ── Normal instruction ────────────────────────────────────────
        _print(_panel(f"[bold white]{cmd}[/]", title="You", style="dim"))
        wb._emit("goal_recorded", {"goal": {"goal_id": f"goal_{uuid4().hex[:8]}", "text": cmd, "status": "active"}})

        action = _run_with_progress(wb)
        _print(*_step_result(action, wb))

    # ── Cleanup ───────────────────────────────────────────────────────
    wb.report()
    _print(f"\n[dim]Report → {wb._store.run_dir / 'report.html'}[/]")
    _print(f"[dim]Notebooks → {wb._store.run_dir / 'notebooks'}[/]")
    return 0


def _run_with_progress(wb) -> str:
    """Run fully automatically until a stopping point.

    One user input runs plan→execute→review→intervene→plan→... continuously.
    Stops only on: waiting_for_human, complete, blocked, error.
    Each execution shows an inline result card.
    """
    phases = {
        "planning": "Planning…", "executing_attempt": "Running code…",
        "reviewing_outcome": "Reviewing…", "diagnosing": "Diagnosing…",
        "planning_intervention": "Planning fix…", "waiting_for_human": "Needs input.",
        "complete": "Done.", "paused": "Paused.",
    }
    action = "no_action"
    try:
        for _ in range(15):  # safety limit — ~5 full plan→execute→review cycles
            snap = wb._store.read_snapshot()
            phase = snap.phase if snap else ""
            _print(f"  [dim]{phases.get(phase, 'Working…')}[/]")
            action = wb.step(1)[0]

            # Show code preview before execution
            if action == "planned_attempt":
                fresh_snap = wb._store.read_snapshot()
                a = next((a for a in fresh_snap.attempts if a.attempt_id == fresh_snap.active_attempt), None)
                if a and a.notebook_cells:
                    code = a.notebook_cells[0].get("source", "") if isinstance(a.notebook_cells[0], dict) else ""
                    lines = code.strip().split("\n")
                    preview = "\n".join(f"  [dim]│[/] {l[:90]}" for l in lines[:8])
                    if len(lines) > 8:
                        preview += f"\n  [dim]│[/] ... ({len(lines)} lines total)"
                    _print(f"  [dim]┌─ code preview ({a.stage or '?'}) ──────────────[/]")
                    _print(preview)
                    _print(f"  [dim]└{'─' * 45}[/]")

            # Show inline result for each execution
            if action == "executed_attempt":
                o = snap.outcomes[-1] if snap.outcomes else None
                a = next((a for a in snap.attempts if a.attempt_id == o.attempt_id), None) if o else None
                obs = len([x for x in snap.observations if x.attempt_id == o.attempt_id]) if o else 0
                status = o.status if o else "?"
                sc = "bright_green" if status == "success" else "bright_red"
                title = a.title if a else "Cell"
                _print(f"  [{sc}]●[/] {title}: [{sc}]{status}[/] · [green]{obs} obs[/]")
                if o and o.metrics:
                    stdout = (o.metrics.get("stdout", "") or "").strip()
                    stderr = (o.metrics.get("stderr", "") or "").strip()
                    if status != "success" and not stderr:
                        stderr = f"Return code: {o.metrics.get('returncode', '?')}. No error output captured."
                    if stdout:
                        _print(f"  [dim]stdout: {stdout[-400:]}[/]")
                    if stderr:
                        _print(f"  [yellow]stderr: {stderr[-600:]}[/]")

            if action == "waiting_for_human":
                break
            if action in ("complete", "blocked", "error"):
                break
            # Everything else → continue looping

        return action
    except Exception as exc:
        _print(_panel(f"[red]{exc}[/]", title="✗ Failed", style="bright_red"))
        return "error"


def _status_line(snap) -> str:
    phase = getattr(snap, "phase", "?")
    pc = {"executing": "green", "reviewing": "bright_yellow", "diagnosing": "yellow",
          "planning": "bright_blue", "waiting_for_human": "bright_red",
          "complete": "green", "paused": "dim"}.get(phase, "dim")
    return (
        f"phase: [{pc}]{phase}[/]  ·  "
        f"attempts: [cyan]{len(getattr(snap, 'attempts', []))}[/]  ·  "
        f"obs: [green]{len(getattr(snap, 'observations', []))}[/]  ·  "
        f"branches: [magenta]{len(getattr(snap, 'branches', []))}[/]"
    )


def _step_result(action: str, wb):
    """Render the result of one step. Returns list of items for _print()."""
    snap = wb._store.read_snapshot()

    if action in ("planned_attempt",):
        a = next((a for a in snap.attempts if a.attempt_id == snap.active_attempt), None)
        body = f"[bold]{a.title if a else 'Planning'}[/]" if a else "Planning next step…"
        if a:
            body += f"\nStage: [bright_cyan]{a.stage}[/]"
            if a.notebook_cells:
                body += f"\nCells: {len(a.notebook_cells)} code blocks"
        return [_render("✦ Plan", body, "bright_blue"), _status_line(snap)]

    if action in ("executed_attempt",):
        a = next((a for a in snap.attempts if a.attempt_id == snap.active_attempt), None)
        o = next((o for o in snap.outcomes if o.attempt_id == snap.active_attempt), None)
        obs_count = len([x for x in snap.observations if x.attempt_id == snap.active_attempt])
        status = o.status if o else "?"
        sc = "bright_green" if status == "success" else "bright_red"
        body = f"[bold]{a.title if a else 'Attempt'}[/] → [{sc}]{status}[/]"
        if obs_count:
            body += f"  ·  [green]{obs_count} observations[/]"
        if o and o.summary:
            body += f"\n[dim]{o.summary[:300]}[/]"

        from blackboard._context import compile_context
        ctx = compile_context(snap)
        for m in ctx.memory[:3]:
            if m.signal == "new":
                continue
            clr = {"conflict": "bright_red", "warning": "bright_yellow", "thin": "dim", "agreement": "bright_green"}.get(m.signal, "dim")
            body += f"\n  [{clr}]● {m.signal.upper()}[/] {m.subject}"

        return [_render("⚡ Run", body, sc), "  " + _status_line(snap)]

    if action in ("applied_intervention", "planned_intervention"):
        recent = snap.interventions[-1] if snap.interventions else None
        body = f"[bold]{recent.intervention_type}[/]\n{recent.summary[:250]}" if recent else "Intervention applied."
        return [_render("⚙ Fix", body, "bright_yellow"), "  " + _status_line(snap)]

    if action == "waiting_for_human":
        for intr in snap.interrupts:
            if intr.status == "open":
                opts = "  ·  ".join(intr.options) if intr.options else "Type your answer below"
                body = f"[bold bright_red]⚠ {intr.question}[/]\n\n{opts}"
                return [_render("⏸ Paused", body, "bright_red")]
        return [_render("⏸ Paused", "Waiting for input.", "bright_red")]

    if action == "complete":
        return [_render("✓ Done", "[bright_green]Analysis complete. Type anything to continue or [bold]quit[/].[/]", "bright_green")]

    if action in ("blocked", "no_snapshot", "no_action"):
        return [_render("✗ Blocked", f"[yellow]Step returned '{action}'.[/]", "bright_yellow")]

    return [_render("· Step", f"Action: {action}", "dim")]


def _render(title: str, body: str, style: str):
    if RICH:
        return Panel(body.strip() or title, title=title, border_style=style, padding=(0, 1))
    return f"[{title}] {body.strip()}"


# ── Run / Doctor / Helpers ───────────────────────────────────────────────

def _print_run_summary(wb):
    s = wb.status
    rpt = wb.report()
    _print(f"\n[bold]Done.[/] {s.get('attempts', 0)} attempts, {s.get('observations', 0)} observations")
    cons = rpt.get("conclusions", []) or rpt.get("summary", {}).get("conclusions", [])
    mem = rpt.get("memory_signals", []) or rpt.get("memory", [])
    cov = rpt.get("coverage", [])
    if cons:
        _print("[bold]Conclusions:[/]")
        for c in (cons if isinstance(cons, list) else [cons]):
            if isinstance(c, dict):
                _print(f"  [{c.get('grade', '?')}] {c.get('text', str(c))[:200]}")
    if mem:
        _print("[bold]Memory:[/]")
        for m in mem[:5]:
            _print(f"  [{m.get('signal', '?')}] {m.get('subject', '')}")
    if cov:
        _print("[bold]Coverage:[/]")
        for c in cov[:5]:
            _print(f"  {c.get('subject', '')}: {c.get('label', '')}")


def _doctor(args) -> int:
    _print("[bold]Environment[/]\n")
    _print(f"  Python: {sys.version.split()[0]}")
    key = os.getenv("OPENAI_API_KEY") or _config().get("openai_api_key")
    vlm_key = os.getenv("BLACKBOARD_VLM_API_KEY") or _config().get("vlm_api_key")
    _print(f"  OPENAI_API_KEY: [{'green' if key else 'dim'}]{'set' if key else 'not set'}[/]")
    _print(f"  VLM (plot viewer): [{'green' if vlm_key else 'dim'}]{'configured' if vlm_key else 'not set — set BLACKBOARD_VLM_API_KEY'}[/]")
    for pkg in ["openai", "fastapi", "uvicorn", "pydantic", "rich"]:
        try:
            __import__(pkg); _print(f"  {pkg}: [green]OK[/]")
        except ImportError:
            _print(f"  {pkg}: [dim]not installed[/]")
    if args.openai and key:
        from blackboard._planner import _call_llm
        try:
            _call_llm("Say OK.", "Respond with {'status':'ok'}.", {"type":"object","properties":{"status":{"type":"string"}},"required":["status"],"additionalProperties":False})
            _print("  OpenAI: [green]connected[/]")
        except Exception as e:
            _print(f"  OpenAI: [red]error — {e}[/]")
    return 0


def _load_domain(name_or_path: str) -> Domain:
    path = Path(name_or_path)
    if path.exists() and path.suffix == ".json":
        return Domain(**json.loads(path.read_text()))
    if name_or_path == "perturbseq":
        from blackboard._domains import perturbseq
        return perturbseq.DOMAIN
    try:
        mod = __import__(name_or_path, fromlist=["DOMAIN"])
        return mod.DOMAIN
    except ImportError:
        return Domain(name=name_or_path, agenda=["inspect", "analyze", "report"])


def _config() -> dict:
    cfg = Path.home() / ".blackboard" / "config.json"
    return json.loads(cfg.read_text()) if cfg.exists() else {}
