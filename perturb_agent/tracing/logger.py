from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class TraceLogger:
    def __init__(self, traces_dir: Path) -> None:
        self.traces_dir = traces_dir
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.tool_calls_path = self.traces_dir / "tool_calls.jsonl"

    @classmethod
    def create(cls, run_dir: Path) -> "TraceLogger":
        return cls(run_dir / "traces")

    def write_tool_call(
        self,
        *,
        tool_use_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        result: str | None = None,
        is_error: bool = False,
    ) -> None:
        self._write_jsonl(
            self.tool_calls_path,
            {
                "timestamp": datetime.now().isoformat(),
                "tool_use_id": tool_use_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "result": result,
                "is_error": is_error,
            },
        )

    def _write_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
