from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.console import Console

from perturb_agent.agents.anthropic_agent import ToolUseAgent
from perturb_agent.chat.transcript import TranscriptWriter
from perturb_agent.runtime.context import RuntimeContext
from perturb_agent.runtime.run_manager import RunManager
from perturb_agent.schemas.base import model_to_json
from perturb_agent.state.manager import StateManager
from perturb_agent.tools.handlers import ToolHandlers
from perturb_agent.tracing import TraceLogger


@dataclass
class GraphRuntime:
    agent: ToolUseAgent
    console: Console
    run_manager: RunManager
    runtime_context: RuntimeContext
    state_manager: StateManager
    tool_handlers: ToolHandlers
    trace_logger: TraceLogger
    transcript: TranscriptWriter

    def save_runtime(self) -> None:
        path = self.run_manager.run_dir / "runtime_context.json"
        path.write_text(model_to_json(self.runtime_context), encoding="utf-8")

    def tool_result(
        self,
        *,
        tool_use_id: str,
        content: str,
        is_error: bool = False,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
        if is_error:
            result["is_error"] = True
        return result
