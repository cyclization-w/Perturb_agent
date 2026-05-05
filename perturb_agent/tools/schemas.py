from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


def validate_model(model_cls, value):
    if hasattr(model_cls, "model_validate"):
        return model_cls.model_validate(value)
    return model_cls.parse_obj(value)


class RunCodeInput(BaseModel):
    code: str
    title: str = "Run Python code"


class RunCodeResult(BaseModel):
    status: Literal["success", "error", "interrupted", "timeout"]
    stdout: str = ""
    stderr: str = ""
    traceback: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    cell_id: str
    notebook_path: str
