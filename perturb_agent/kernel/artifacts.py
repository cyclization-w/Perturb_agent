from __future__ import annotations

import base64
from pathlib import Path

from perturb_agent.schemas.artifacts import Artifact


class ArtifactManager:
    def __init__(self, figures_dir: Path) -> None:
        self.figures_dir = figures_dir
        self.figures_dir.mkdir(parents=True, exist_ok=True)

    def save_display_artifacts(
        self,
        *,
        cell_id: str,
        output_index: int,
        data: dict,
    ) -> list[Artifact]:
        artifacts: list[Artifact] = []
        if "image/png" in data:
            artifact_id = f"{cell_id}_figure_{output_index:03d}"
            path = self.figures_dir / f"{artifact_id}.png"
            path.write_bytes(base64.b64decode(data["image/png"]))
            artifacts.append(
                Artifact(
                    id=artifact_id,
                    kind="figure",
                    path=path,
                    mime_type="image/png",
                    source_cell_id=cell_id,
                )
            )
        return artifacts

