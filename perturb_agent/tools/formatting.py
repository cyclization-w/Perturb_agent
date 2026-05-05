from __future__ import annotations

from perturb_agent.schemas.execution import CellExecutionResult
from perturb_agent.tools.schemas import RunCodeResult


def truncate_text(text: str, limit: int = 6000) -> str:
    if len(text) <= limit:
        return text
    suffix = f"\n\n...[truncated {len(text) - limit} characters]"
    return text[:limit] + suffix


def format_run_code_result(result: CellExecutionResult, notebook_path: str) -> RunCodeResult:
    return RunCodeResult(
        status=result.status,
        stdout=truncate_text(result.stdout),
        stderr=truncate_text(result.stderr),
        traceback=result.traceback[-8:],
        artifacts=[str(artifact.path) for artifact in result.artifacts],
        cell_id=result.cell_id,
        notebook_path=notebook_path,
    )

