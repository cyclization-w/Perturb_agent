from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()


def model_to_json(model: BaseModel) -> str:
    return json.dumps(model_to_dict(model), ensure_ascii=False, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)

