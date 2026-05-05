from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console

from perturb_agent.agents.anthropic_agent import AnthropicToolUseAgent, AgentResponse
from perturb_agent.agents.prompts import GRAPH_AGENT_SYSTEM_PROMPT
from perturb_agent.chat.transcript import TranscriptWriter
from perturb_agent.graph.agent_state import AgentGraphState
from perturb_agent.graph.langgraph_builder import build_agent_graph
from perturb_agent.graph.runtime import GraphRuntime
from perturb_agent.kernel.session import KernelSession
from perturb_agent.runtime.context import RuntimeContext
from perturb_agent.runtime.run_manager import RunManager
from perturb_agent.state.manager import StateManager
from perturb_agent.tools.definitions import TOOL_DEFINITIONS
from perturb_agent.tools.handlers import ToolHandlers
from perturb_agent.tracing import TraceLogger


class GraphAgentSession:
    def __init__(
        self,
        *,
        run_manager: RunManager,
        project_root: Path,
        console: Console,
        agent_backend: str,
        model: str | None = None,
    ) -> None:
        self.run_manager = run_manager
        self.console = console
        self.runtime_context = RuntimeContext(
            project_root=project_root.resolve(),
            run_id=run_manager.run_id,
            run_dir=run_manager.run_dir.resolve(),
            notebook_path=run_manager.notebook_path.resolve(),
            artifacts_dir=run_manager.artifacts_dir.resolve(),
        )
        self.state_manager = StateManager.create(run_manager.run_dir)
        self.kernel = KernelSession(
            figures_dir=run_manager.figures_dir,
            runtime_dir=run_manager.run_dir / "kernel",
        )
        self.tool_handlers = ToolHandlers(
            kernel=self.kernel,
            run_manager=run_manager,
            runtime=self.runtime_context,
        )
        self.transcript = TranscriptWriter(run_manager.run_dir / "messages.jsonl")
        if agent_backend == "anthropic":
            agent = AnthropicToolUseAgent(
                model=model,
                system=GRAPH_AGENT_SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
            )
        else:
            agent = GraphMockToolUseAgent()

        self.runtime = GraphRuntime(
            agent=agent,
            console=console,
            run_manager=run_manager,
            runtime_context=self.runtime_context,
            state_manager=self.state_manager,
            tool_handlers=self.tool_handlers,
            trace_logger=TraceLogger.create(run_manager.run_dir),
            transcript=self.transcript,
        )
        self.graph = build_agent_graph(self.runtime)
        self.config = {"configurable": {"thread_id": run_manager.run_id}}
        self.runtime.save_runtime()

    async def handle_user_message(self, user_text: str) -> None:
        if self.runtime_context.pending_tool_id:
            tool_result = {
                "type": "tool_result",
                "tool_use_id": self.runtime_context.pending_tool_id,
                "content": user_text,
            }
            tool_results = [*self.runtime_context.pending_tool_results, tool_result]
            self.runtime.agent.append_tool_results(tool_results)
            self.transcript.write("tool_results", {"content": tool_results})
            self.runtime_context.pending_tool_id = None
            self.runtime_context.pending_tool_results = []
        else:
            self.runtime.agent.append_user_text(user_text)
            self.transcript.write("user", {"content": user_text})

        initial_state: AgentGraphState = {
            "run_id": self.run_manager.run_id,
            "user_input": user_text,
            "messages": self.runtime.agent.messages,
            "analysis_state_summary": None,
            "pending_tool_calls": [],
            "tool_results": [],
            "pending_pi_question": None,
            "current_tier": None,
            "finished": self.runtime_context.finished,
        }
        await self.graph.ainvoke(initial_state, config=self.config)
        self.runtime.save_runtime()

    async def close(self) -> None:
        if self.runtime_context.kernel_started:
            await self.kernel.shutdown()
            self.run_manager.event_log.write("kernel_shutdown", {})
        self.runtime.save_runtime()


class GraphMockToolUseAgent:
    """Offline graph agent for deterministic state/tier/Jupyter smoke tests."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._turn = 0

    async def step(self) -> AgentResponse:
        self._turn += 1
        last_result = self._last_tool_result_text()
        user_text = self._last_user_text()

        if "检查运行环境" in user_text or "runtime" in user_text.lower():
            return self._runtime_path(last_result)
        return self._state_path(last_result)

    def append_user_text(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def append_assistant_content(self, content: list[dict[str, Any]]) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def append_tool_results(self, results: list[dict[str, Any]]) -> None:
        self.messages.append({"role": "user", "content": results})

    def _state_path(self, last_result: str) -> AgentResponse:
        if not last_result:
            return AgentResponse(
                content=[
                    {"type": "text", "text": "我先读取 analysis_state，确认当前缺哪些设计信息。"},
                    {
                        "type": "tool_use",
                        "id": f"mock_query_{self._turn}",
                        "name": "query_state",
                        "input": {"section": "all"},
                    },
                ]
            )
        return AgentResponse(
            content=[
                {
                    "type": "text",
                    "text": "当前实验设计仍有 blocking unknown，需要先问 PI，而不是猜测。",
                },
                {
                    "type": "tool_use",
                    "id": f"mock_pi_{self._turn}",
                    "name": "request_pi_input",
                    "input": {
                        "question": "这份数据是 low-MOI 还是 high-MOI？non-targeting control 的标签是什么？",
                        "context_summary": "analysis_state 中 MOI、control labels、guide/target columns 仍为 unknown。",
                        "options": [
                            {
                                "label": "提供实验设计",
                                "implications": "Agent 可以进入 load/QC 前的设计确认。",
                            }
                        ],
                        "your_recommendation": "先确认 MOI 和 NTC 标签。",
                        "urgency": "blocking",
                    },
                },
            ]
        )

    def _runtime_path(self, last_result: str) -> AgentResponse:
        if not last_result:
            return AgentResponse(
                content=[
                    {"type": "text", "text": "这是低风险技术检查，我会先记录 Tier A 决策。"},
                    {
                        "type": "tool_use",
                        "id": f"mock_propose_{self._turn}",
                        "name": "propose_decision",
                        "input": {
                            "decision_id": "runtime.check_environment",
                            "stage": "load",
                            "tier": "A",
                            "tier_reason": "Checking runtime paths is technical, reversible, and low-risk.",
                            "requires_pi": False,
                            "reversibility": "trivial",
                            "decision": {"action": "execute_python_environment_probe"},
                            "reasoning": "We need to confirm the Jupyter kernel can see PROJECT_ROOT and RUN_DIR.",
                            "expected_outcome": "Python prints PROJECT_ROOT, RUN_DIR, and a probe value.",
                            "evidence_refs": [],
                            "alternatives_considered": [],
                        },
                    },
                ]
            )
        if "runtime.check_environment" in last_result and '"status": "proposed"' in last_result:
            return AgentResponse(
                content=[
                    {
                        "type": "tool_use",
                        "id": f"mock_commit_{self._turn}",
                        "name": "commit_decision",
                        "input": {"decision_id": "runtime.check_environment"},
                    }
                ]
            )
        if "runtime.check_environment" in last_result and '"status": "committed"' in last_result:
            return AgentResponse(
                content=[
                    {
                        "type": "tool_use",
                        "id": f"mock_execute_{self._turn}",
                        "name": "execute_python",
                        "input": {
                            "title": "Check Jupyter runtime context",
                            "code": (
                                "print('PROJECT_ROOT =', PROJECT_ROOT)\n"
                                "print('RUN_DIR =', RUN_DIR)\n"
                                "probe_value = 42\n"
                                "print('probe_value =', probe_value)"
                            ),
                        },
                    }
                ]
            )
        return AgentResponse(
            content=[
                {"type": "text", "text": "运行环境检查完成，Jupyter kernel 已经可用。"},
                {
                    "type": "tool_use",
                    "id": f"mock_finish_{self._turn}",
                    "name": "finish",
                    "input": {"summary": "Graph mock completed a Tier A Jupyter runtime check."},
                },
            ]
        )

    def _last_tool_result_text(self) -> str:
        if not self.messages:
            return ""
        content = self.messages[-1].get("content", "")
        if isinstance(content, list):
            return "\n".join(str(item.get("content", "")) for item in content)
        return ""

    def _last_user_text(self) -> str:
        for message in reversed(self.messages):
            content = message.get("content", "")
            if message.get("role") == "user" and isinstance(content, str):
                return content
        return ""
