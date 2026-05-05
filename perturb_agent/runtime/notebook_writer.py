from __future__ import annotations

from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_notebook, new_output

from perturb_agent.schemas.execution import CellExecutionResult


class NotebookWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.nb = new_notebook(
            metadata={
                "kernelspec": {
                    "display_name": "Python 3",
                    "language": "python",
                    "name": "python3",
                },
                "language_info": {"name": "python", "pygments_lexer": "ipython3"},
            }
        )

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.save()

    def append_result(self, result: CellExecutionResult) -> None:
        cell = new_code_cell(source=result.code)
        cell["execution_count"] = result.execution_count
        cell["metadata"]["cell_id"] = result.cell_id
        cell["outputs"] = [self._to_nb_output(output) for output in result.outputs]
        self.nb.cells.append(cell)
        self.save()

    def save(self) -> None:
        nbformat.write(self.nb, self.path)

    def _to_nb_output(self, output):
        if output.output_type == "stream":
            return new_output(
                output_type="stream",
                name=output.name or "stdout",
                text=output.text or "",
            )
        if output.output_type == "execute_result":
            return new_output(
                output_type="execute_result",
                data=output.data,
                metadata={},
                execution_count=None,
            )
        if output.output_type == "display_data":
            return new_output(output_type="display_data", data=output.data, metadata={})
        if output.output_type == "error":
            return new_output(
                output_type="error",
                ename=output.ename or "Error",
                evalue=output.evalue or "",
                traceback=output.traceback,
            )
        raise ValueError(f"Unsupported output type: {output.output_type}")

