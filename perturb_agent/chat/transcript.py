from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TranscriptWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event_type: str, payload: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"type": event_type, "payload": payload},
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )

