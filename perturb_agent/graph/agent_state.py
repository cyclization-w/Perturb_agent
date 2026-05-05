from __future__ import annotations

from typing import Any, TypedDict


class AgentGraphState(TypedDict, total=False):
    run_id: str
    user_input: str | None
    messages: list[dict[str, Any]]
    analysis_state_summary: dict[str, Any] | None
    pending_tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    pending_pi_question: str | None
    current_tier: str | None
    finished: bool
