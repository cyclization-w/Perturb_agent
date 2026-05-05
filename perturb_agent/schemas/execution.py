from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from perturb_agent.schemas.artifacts import Artifact


class CellOutput(BaseModel):
    output_type: Literal["stream", "execute_result", "display_data", "error"]
    name: str | None = None
    text: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    ename: str | None = None
    evalue: str | None = None
    traceback: list[str] = Field(default_factory=list)


class CellExecutionResult(BaseModel):
    cell_id: str
    code: str
    status: Literal["success", "error", "interrupted", "timeout"]
    stdout: str = ""
    stderr: str = ""
    traceback: list[str] = Field(default_factory=list)
    outputs: list[CellOutput] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    execution_count: int | None = None
    started_at: datetime
    finished_at: datetime

    @property
    def ok(self) -> bool:
        return self.status == "success"


class AnalysisStep(BaseModel):
    step_id: str
    title: str
    code: str
    description: str = ""

