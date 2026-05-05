from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class Artifact(BaseModel):
    id: str
    kind: Literal["figure", "table", "text", "other"]
    path: Path
    mime_type: str | None = None
    source_cell_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

