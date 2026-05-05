from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console

from perturb_agent.console_ui import print_completion_summary, print_start_banner, print_user_message
from perturb_agent.graph.langgraph_session import GraphAgentSession
from perturb_agent.runtime.run_manager import RunManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="perturb-agent",
        description="LangGraph state/tier-aware analysis agent backed by a Jupyter kernel.",
    )
    subparsers = parser.add_subparsers(dest="command")

    graph_chat = subparsers.add_parser(
        "graph-chat",
        help="Start the LangGraph-backed state/tier-aware agent loop.",
    )
    graph_chat.add_argument(
        "--runs-dir",
        default="runs",
        help="Directory where graph chat run artifacts are written.",
    )
    graph_chat.add_argument(
        "--project-root",
        default=".",
        help="Project directory exposed to the Jupyter kernel.",
    )
    graph_chat.add_argument(
        "--agent",
        choices=["mock", "anthropic"],
        default="mock",
        help="Agent backend. anthropic reads ANTHROPIC_API_KEY.",
    )
    graph_chat.add_argument(
        "--model",
        default=None,
        help="Model name for the selected backend.",
    )
    graph_chat.add_argument(
        "--message",
        action="append",
        help="Run one or more messages non-interactively, then exit.",
    )

    return parser


async def run_graph_chat(args: argparse.Namespace) -> int:
    console = Console()
    run_manager = RunManager.create(Path(args.runs_dir))
    project_root = Path(args.project_root)
    session = GraphAgentSession(
        run_manager=run_manager,
        project_root=project_root,
        console=console,
        agent_backend=args.agent,
        model=args.model,
    )

    print_start_banner(
        console,
        run_dir=run_manager.run_dir,
        agent_backend=args.agent,
        model=args.model,
        project_root=project_root.resolve(),
    )

    try:
        if args.message:
            for message in args.message:
                print_user_message(console, message)
                if message.strip().lower() in {"exit", "quit", "q"}:
                    break
                await session.handle_user_message(message)
                if session.runtime_context.finished:
                    break
            return 0

        while not session.runtime_context.finished:
            try:
                user_text = await asyncio.to_thread(input, "\nYou > ")
            except (EOFError, KeyboardInterrupt):
                console.print()
                break
            if user_text.strip().lower() in {"exit", "quit", "q"}:
                break
            await session.handle_user_message(user_text)
    finally:
        await session.close()
        print_completion_summary(
            console,
            paths={
                "Notebook": run_manager.notebook_path,
                "Events": run_manager.events_path,
                "Messages": run_manager.run_dir / "messages.jsonl",
                "Runtime": run_manager.run_dir / "runtime_context.json",
                "Analysis state": run_manager.run_dir / "analysis_state.json",
                "Tool trace": run_manager.run_dir / "traces" / "tool_calls.jsonl",
            },
        )

    return 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "graph-chat":
        if sys.platform.startswith("win"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        raise SystemExit(asyncio.run(run_graph_chat(args)))

    parser.print_help()
    raise SystemExit(2)
