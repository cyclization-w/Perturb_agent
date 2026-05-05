from __future__ import annotations

import json
from typing import Any

from perturb_agent.console_ui import (
    print_agent_text,
    print_finish_summary,
    print_inspection_summary,
    print_pi_question,
    print_tool_start,
)
from perturb_agent.graph.agent_state import AgentGraphState
from perturb_agent.graph.runtime import GraphRuntime
from perturb_agent.schemas.base import model_to_dict, model_to_json
from perturb_agent.state.schemas import DecisionRecord
from perturb_agent.tier.rules import TierValidationError, validate_commit_allowed, validate_proposal
from perturb_agent.tools.mutation_schemas import (
    CommitDecisionInput,
    ExecutePythonInput,
    GraphFinishInput,
    InspectAnnDataInput,
    ProposeDecisionInput,
    QueryStateInput,
    RequestPiInput,
)
from perturb_agent.tools.schemas import validate_model


async def load_analysis_state(
    state: AgentGraphState,
    *,
    runtime: GraphRuntime,
) -> AgentGraphState:
    return {
        **state,
        "analysis_state_summary": runtime.state_manager.snapshot(),
        "messages": runtime.agent.messages,
    }


async def llm_decide(
    state: AgentGraphState,
    *,
    runtime: GraphRuntime,
) -> AgentGraphState:
    response = await runtime.agent.step()
    runtime.agent.append_assistant_content(response.content)
    runtime.transcript.write("assistant", {"content": response.content})

    tool_uses: list[dict[str, Any]] = []
    for block in response.content:
        block_type = block.get("type")
        if block_type == "text" and block.get("text"):
            print_agent_text(runtime.console, block["text"])
        elif block_type == "tool_use":
            tool_uses.append(block)

    return {
        **state,
        "messages": runtime.agent.messages,
        "pending_tool_calls": tool_uses,
        "tool_results": [],
    }


async def execute_tool(
    state: AgentGraphState,
    *,
    runtime: GraphRuntime,
) -> AgentGraphState:
    tool_results: list[dict[str, Any]] = []
    pending_question: str | None = None
    current_tier: str | None = state.get("current_tier")
    finished = bool(state.get("finished", False))

    for tool_use in state.get("pending_tool_calls", []):
        name = tool_use["name"]
        tool_use_id = tool_use["id"]
        tool_input = tool_use.get("input", {})
        try:
            if name == "query_state":
                result = _handle_query_state(tool_input, runtime)
            elif name == "inspect_anndata":
                result = await _handle_inspect_anndata(tool_input, runtime)
            elif name == "propose_decision":
                result, current_tier = _handle_propose_decision(tool_input, runtime)
            elif name == "commit_decision":
                result = _handle_commit_decision(tool_input, runtime)
            elif name == "execute_python":
                result = await _handle_execute_python(tool_input, runtime)
            elif name == "request_pi_input":
                result, pending_question = _handle_request_pi_input(
                    tool_input,
                    tool_use_id,
                    runtime,
                )
            elif name == "finish":
                result, finished = _handle_finish(tool_input, runtime)
            else:
                result = _json_error(f"Unknown tool: {name}")
        except Exception as exc:
            result = _json_error(str(exc))
        runtime.trace_logger.write_tool_call(
            tool_use_id=tool_use_id,
            tool_name=name,
            tool_input=tool_input,
            result=result,
            is_error=_is_error_json(result),
        )

        if pending_question:
            runtime.runtime_context.pending_tool_id = tool_use_id
            runtime.runtime_context.pending_tool_results = tool_results
            runtime.run_manager.event_log.write(
                "request_pi_input",
                {"tool_use_id": tool_use_id, "question": pending_question},
            )
            break
        tool_results.append(
            runtime.tool_result(
                tool_use_id=tool_use_id,
                content=result,
                is_error=_is_error_json(result),
            )
        )

    runtime.save_runtime()
    return {
        **state,
        "tool_results": tool_results,
        "pending_pi_question": pending_question,
        "current_tier": current_tier,
        "finished": finished,
    }


async def persist_result(
    state: AgentGraphState,
    *,
    runtime: GraphRuntime,
) -> AgentGraphState:
    tool_results = state.get("tool_results", [])
    if tool_results:
        runtime.agent.append_tool_results(tool_results)
        runtime.transcript.write("tool_results", {"content": tool_results})
    runtime.save_runtime()
    return {
        **state,
        "messages": runtime.agent.messages,
        "pending_tool_calls": [],
        "tool_results": [],
    }


def route_after_llm(state: AgentGraphState) -> str:
    if state.get("finished"):
        return "end"
    if state.get("pending_tool_calls"):
        return "execute_tool"
    return "end"


def route_after_execute(state: AgentGraphState) -> str:
    if state.get("pending_pi_question") or state.get("finished"):
        return "end"
    return "persist_result"


def _handle_query_state(tool_input: dict[str, Any], runtime: GraphRuntime) -> str:
    parsed = validate_model(QueryStateInput, tool_input)
    data = runtime.state_manager.query(parsed.section)
    runtime.run_manager.event_log.write(
        "query_state",
        {"section": parsed.section or "all"},
    )
    return json.dumps(data, ensure_ascii=False, default=str)


async def _handle_inspect_anndata(tool_input: dict[str, Any], runtime: GraphRuntime) -> str:
    parsed = validate_model(InspectAnnDataInput, tool_input)
    print_tool_start(runtime.console, "Inspect AnnData", parsed.path)
    result = await runtime.tool_handlers.inspect_anndata(
        {"path": parsed.path, "dataset_id": parsed.dataset_id},
    )
    content = result["content"]
    try:
        payload = json.loads(content)
        inspection = payload.get("inspection") or {}
    except json.JSONDecodeError:
        inspection = {"error": "inspect_anndata returned non-JSON content"}

    if "error" not in inspection:
        runtime.state_manager.apply_anndata_inspection(inspection)
        runtime.run_manager.event_log.write(
            "inspect_anndata",
            {
                "path": inspection.get("path"),
                "shape": inspection.get("shape"),
                "dataset_likelihood": inspection.get("dataset_likelihood"),
                "missing_design_fields": inspection.get("missing_design_fields"),
            },
        )

    _print_inspection_result(content, runtime)
    return content


def _handle_propose_decision(
    tool_input: dict[str, Any],
    runtime: GraphRuntime,
) -> tuple[str, str]:
    parsed = validate_model(ProposeDecisionInput, tool_input)
    decision = DecisionRecord(**model_to_dict(parsed))
    validate_proposal(decision)
    runtime.state_manager.append_decision(decision)
    runtime.run_manager.event_log.write("propose_decision", model_to_dict(decision))
    return model_to_json(decision), decision.tier


def _handle_commit_decision(tool_input: dict[str, Any], runtime: GraphRuntime) -> str:
    parsed = validate_model(CommitDecisionInput, tool_input)
    state = runtime.state_manager.load()
    proposal: DecisionRecord | None = None
    for decision in reversed(state.decisions):
        if decision.decision_id == parsed.decision_id:
            proposal = decision
            break
    if proposal is None:
        raise KeyError(f"No proposed decision found for decision_id={parsed.decision_id!r}")

    proposal.pi_confirmation_ref = parsed.pi_confirmation_ref
    proposal.modifications_from_proposal = parsed.modifications_from_proposal
    validate_commit_allowed(proposal)
    committed = runtime.state_manager.commit_decision(
        decision_id=parsed.decision_id,
        pi_confirmation_ref=parsed.pi_confirmation_ref,
        modifications_from_proposal=parsed.modifications_from_proposal,
    )
    runtime.run_manager.event_log.write("commit_decision", model_to_dict(committed))
    return model_to_json(committed)


async def _handle_execute_python(tool_input: dict[str, Any], runtime: GraphRuntime) -> str:
    parsed = validate_model(ExecutePythonInput, tool_input)
    print_tool_start(runtime.console, "Run Python", parsed.title)
    result = await runtime.tool_handlers.run_code(
        {"code": parsed.code, "title": parsed.title},
    )
    content = result["content"]
    _print_run_code_result(content, runtime)
    return content


def _handle_request_pi_input(
    tool_input: dict[str, Any],
    tool_use_id: str,
    runtime: GraphRuntime,
) -> tuple[str, str]:
    parsed = validate_model(RequestPiInput, tool_input)
    print_pi_question(runtime.console, parsed)
    runtime.runtime_context.pending_tool_id = tool_use_id
    runtime.runtime_context.finished = False
    return "", parsed.question


def _handle_finish(tool_input: dict[str, Any], runtime: GraphRuntime) -> tuple[str, bool]:
    parsed = validate_model(GraphFinishInput, tool_input)
    print_finish_summary(runtime.console, parsed.summary)
    runtime.runtime_context.finished = True
    runtime.run_manager.event_log.write("finish", {"summary": parsed.summary})
    return json.dumps({"summary": parsed.summary}, ensure_ascii=False), True


def _print_run_code_result(content: str, runtime: GraphRuntime) -> None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        runtime.console.print(content)
        return

    stdout = parsed.get("stdout") or ""
    stderr = parsed.get("stderr") or ""
    traceback = parsed.get("traceback") or []
    artifacts = parsed.get("artifacts") or []
    if stdout:
        runtime.console.print(stdout.rstrip())
    if stderr:
        runtime.console.print(f"[red]{stderr.rstrip()}[/]")
    if traceback:
        runtime.console.print(f"[red]Code cell failed:[/] {_summarize_traceback(traceback)}")
        runtime.console.print("[dim]Full traceback is saved in notebook.ipynb, events.jsonl, and tool trace.[/]")
    for artifact in artifacts:
        runtime.console.print(f"[magenta]Artifact:[/] {artifact}")


def _print_inspection_result(content: str, runtime: GraphRuntime) -> None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        runtime.console.print(content)
        return

    inspection = payload.get("inspection") or {}
    if inspection.get("error"):
        runtime.console.print(f"[red]{inspection['error']}[/]")
        return

    print_inspection_summary(runtime.console, inspection)


def _json_error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _summarize_traceback(traceback: list[str]) -> str:
    for line in reversed(traceback):
        stripped = line.strip()
        if stripped and not stripped.startswith("File "):
            return stripped
    return "See persisted logs for details."


def _is_error_json(content: str) -> bool:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return False
    return "error" in parsed
