from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from dotenv import find_dotenv, load_dotenv

from perturb_agent.agents.prompts import PHD_AGENT_SYSTEM_PROMPT
from perturb_agent.tools.definitions import TOOL_DEFINITIONS


@dataclass
class AgentResponse:
    content: list[dict[str, Any]]


class ToolUseAgent(Protocol):
    messages: list[dict[str, Any]]

    async def step(self) -> AgentResponse:
        ...

    def append_user_text(self, text: str) -> None:
        ...

    def append_assistant_content(self, content: list[dict[str, Any]]) -> None:
        ...

    def append_tool_results(self, results: list[dict[str, Any]]) -> None:
        ...


class AnthropicToolUseAgent:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        system: str = PHD_AGENT_SYSTEM_PROMPT,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        load_dotenv(find_dotenv(usecwd=True), override=False)
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        self.base_url = (base_url or os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")).rstrip("/")
        self.system = system
        self.tools = tools or TOOL_DEFINITIONS
        self.messages: list[dict[str, Any]] = []
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for AnthropicToolUseAgent.")

    async def step(self) -> AgentResponse:
        return await asyncio.to_thread(self._step_sync)

    def append_user_text(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def append_assistant_content(self, content: list[dict[str, Any]]) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def append_tool_results(self, results: list[dict[str, Any]]) -> None:
        self.messages.append({"role": "user", "content": results})

    def _step_sync(self) -> AgentResponse:
        payload = {
            "model": self.model,
            "system": self.system,
            "messages": self.messages,
            "tools": self.tools,
            "max_tokens": 4096,
        }
        request = urllib.request.Request(
            url=f"{self.base_url}/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = json.loads(response.read().decode("utf-8"))
        return AgentResponse(content=raw.get("content", []))


class MockToolUseAgent:
    """Offline agent used to prove the tool-use loop without an API key."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._turn = 0

    async def step(self) -> AgentResponse:
        self._turn += 1
        last_tool_result = self._last_tool_result_text()
        if last_tool_result and "cell_id" in last_tool_result:
            return AgentResponse(
                content=[
                    {
                        "type": "text",
                        "text": "我已经在 Jupyter kernel 中执行了代码，并看到了工具结果。这个最小闭环工作正常。",
                    },
                    {
                        "type": "tool_use",
                        "id": f"mock_finish_{self._turn}",
                        "name": "finish",
                        "input": {"summary": "Mock agent completed one run_code observation loop."},
                    },
                ]
            )

        return AgentResponse(
            content=[
                {
                    "type": "text",
                    "text": "我先运行一个很小的 Python 检查，确认 kernel 和上下文可用。",
                },
                {
                    "type": "tool_use",
                    "id": f"mock_run_{self._turn}",
                    "name": "run_code",
                    "input": {
                        "title": "Check Jupyter runtime context",
                        "code": (
                            "print('PROJECT_ROOT =', PROJECT_ROOT)\n"
                            "print('RUN_DIR =', RUN_DIR)\n"
                            "probe_value = 42\n"
                            "print('probe_value =', probe_value)"
                        ),
                    },
                },
            ]
        )

    def append_user_text(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def append_assistant_content(self, content: list[dict[str, Any]]) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def append_tool_results(self, results: list[dict[str, Any]]) -> None:
        self.messages.append({"role": "user", "content": results})

    def _last_tool_result_text(self) -> str:
        if not self.messages:
            return ""
        content = self.messages[-1].get("content", "")
        if isinstance(content, list):
            return "\n".join(str(item.get("content", "")) for item in content)
        return str(content)
