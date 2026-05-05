from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def print_start_banner(
    console: Console,
    *,
    run_dir: Path,
    agent_backend: str,
    model: str | None,
    project_root: Path,
) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column()
    table.add_row("Run", str(run_dir))
    table.add_row("Agent", agent_backend)
    table.add_row("Model", model or "default")
    table.add_row("Project", str(project_root))
    console.print(
        Panel(
            Group(
                Text("Perturb Agent Graph Chat", style="bold"),
                Text("State-aware, tier-aware analysis in a persistent Jupyter kernel.", style="dim"),
                table,
                Text("Type exit / quit / q to end.", style="dim"),
            ),
            border_style="cyan",
        )
    )


def print_completion_summary(console: Console, *, paths: dict[str, Path]) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column()
    for label, path in paths.items():
        table.add_row(label, str(path))
    console.print(Panel(table, title="Run Complete", border_style="green"))


def print_user_message(console: Console, text: str) -> None:
    console.print(Panel(text, title="You", border_style="blue"))


def print_agent_text(console: Console, text: str) -> None:
    console.print(Panel(text.strip(), title="Agent", border_style="cyan"))


def print_tool_start(console: Console, title: str, detail: str | None = None) -> None:
    body = Text(title, style="bold")
    if detail:
        body.append("\n")
        body.append(detail, style="dim")
    console.print(Panel(body, title="Tool", border_style="magenta"))


def print_pi_question(console: Console, payload: Any) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold yellow")
    table.add_column()
    table.add_row("Question", payload.question)
    table.add_row("Urgency", payload.urgency)
    table.add_row("Context", payload.context_summary)
    if payload.your_recommendation:
        table.add_row("Recommendation", payload.your_recommendation)

    option_table = Table(title="Options", show_lines=False)
    option_table.add_column("Option", style="bold")
    option_table.add_column("Implication")
    for option in payload.options:
        option_table.add_row(option.get("label", ""), option.get("implications", ""))

    console.print(
        Panel(
            Group(table, option_table),
            title="PI Input Needed",
            border_style="yellow",
        )
    )


def print_finish_summary(console: Console, summary: str) -> None:
    console.print(Panel(summary, title="Summary", border_style="green"))


def print_inspection_summary(console: Console, inspection: dict[str, Any]) -> None:
    shape = inspection.get("shape") or {}
    likelihood = inspection.get("dataset_likelihood") or {}
    candidates = inspection.get("candidate_columns") or {}
    questions = inspection.get("recommended_pi_questions") or []

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column()
    table.add_row("Shape", f"{shape.get('n_cells', '?')} cells x {shape.get('n_genes', '?')} genes")
    table.add_row("Perturb-seq", str(likelihood.get("perturbseq", "unknown")))
    table.add_row("Ordinary scRNA", str(likelihood.get("ordinary_scrna", "unknown")))

    candidate_table = Table(title="Candidate Columns", show_lines=False)
    candidate_table.add_column("Role", style="bold")
    candidate_table.add_column("Columns")
    for name in ("cell_type", "condition", "guide", "target", "batch", "control"):
        values = candidates.get(name) or []
        if values:
            candidate_table.add_row(name, ", ".join(values[:6]))

    if questions:
        question_table = Table(title="PI Question Candidates", show_lines=False)
        question_table.add_column("Question")
        for question in questions[:4]:
            question_table.add_row(str(question))
        content = Group(table, candidate_table, question_table)
    else:
        content = Group(table, candidate_table)

    console.print(Panel(content, title="AnnData Inspection", border_style="cyan"))
