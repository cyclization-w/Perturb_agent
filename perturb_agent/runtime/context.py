from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class RuntimeContext(BaseModel):
    project_root: Path
    run_id: str
    run_dir: Path
    notebook_path: Path
    artifacts_dir: Path
    cell_counter: int = 0
    kernel_started: bool = False
    bootstrap_done: bool = False
    pending_tool_id: str | None = None
    pending_tool_results: list[dict] = []
    finished: bool = False
