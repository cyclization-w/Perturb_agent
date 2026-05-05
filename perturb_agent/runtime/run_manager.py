from __future__ import annotations

from datetime import datetime
from pathlib import Path

from perturb_agent.runtime.event_log import EventLog
from perturb_agent.runtime.notebook_writer import NotebookWriter


class RunManager:
    def __init__(self, run_id: str, run_dir: Path) -> None:
        self.run_id = run_id
        self.run_dir = run_dir
        self.artifacts_dir = run_dir / "artifacts"
        self.figures_dir = self.artifacts_dir / "figures"
        self.notebook_path = run_dir / "notebook.ipynb"
        self.events_path = run_dir / "events.jsonl"
        self.event_log = EventLog(self.events_path)
        self.notebook = NotebookWriter(self.notebook_path)

    @classmethod
    def create(cls, runs_dir: Path) -> "RunManager":
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = runs_dir / run_id
        suffix = 1
        while run_dir.exists():
            suffix += 1
            run_dir = runs_dir / f"{run_id}_{suffix:02d}"
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "artifacts" / "figures").mkdir(parents=True, exist_ok=True)
        manager = cls(run_id=run_dir.name, run_dir=run_dir)
        manager.notebook.initialize()
        manager.event_log.write("run_created", {"run_id": manager.run_id, "run_dir": str(run_dir)})
        return manager
