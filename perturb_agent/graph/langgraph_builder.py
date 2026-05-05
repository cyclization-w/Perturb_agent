from __future__ import annotations

from typing import Any

from perturb_agent.graph.agent_state import AgentGraphState
from perturb_agent.graph.langgraph_nodes import (
    execute_tool,
    llm_decide,
    load_analysis_state,
    persist_result,
    route_after_execute,
    route_after_llm,
)
from perturb_agent.graph.runtime import GraphRuntime


def build_agent_graph(runtime: GraphRuntime) -> Any:
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph is required for graph-chat. Install with `pip install -e .[agent]`."
        ) from exc

    try:
        from langgraph.checkpoint.memory import InMemorySaver

        checkpointer = InMemorySaver()
    except ImportError:
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()

    graph = StateGraph(AgentGraphState)

    async def load_node(state: AgentGraphState) -> AgentGraphState:
        return await load_analysis_state(state, runtime=runtime)

    async def decide_node(state: AgentGraphState) -> AgentGraphState:
        return await llm_decide(state, runtime=runtime)

    async def execute_node(state: AgentGraphState) -> AgentGraphState:
        return await execute_tool(state, runtime=runtime)

    async def persist_node(state: AgentGraphState) -> AgentGraphState:
        return await persist_result(state, runtime=runtime)

    graph.add_node("load_analysis_state", load_node)
    graph.add_node("llm_decide", decide_node)
    graph.add_node("execute_tool", execute_node)
    graph.add_node("persist_result", persist_node)

    graph.set_entry_point("load_analysis_state")
    graph.add_edge("load_analysis_state", "llm_decide")
    graph.add_conditional_edges(
        "llm_decide",
        route_after_llm,
        {"execute_tool": "execute_tool", "end": END},
    )
    graph.add_conditional_edges(
        "execute_tool",
        route_after_execute,
        {"persist_result": "persist_result", "end": END},
    )
    graph.add_edge("persist_result", "llm_decide")

    return graph.compile(checkpointer=checkpointer)
