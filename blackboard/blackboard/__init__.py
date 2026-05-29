"""Blackboard — LLM-driven analysis with provenance memory.

Usage:
    from blackboard import Workbench, Domain

    domain = Domain(
        name="my_domain",
        agenda=["stage1", "stage2", "report"],
        capabilities=[{"id": "my.inspect", "stage": "stage1", "description": "..."}],
        rubric=["Check X before concluding Y.", "..."],
    )

    wb = Workbench(domain=domain)
    wb.run("./workspace", goal="Analyze this dataset", steps=5)
    print(wb.report())
    wb.serve()  # Start GUI at http://127.0.0.1:8765
"""

from blackboard._workbench import Workbench
from blackboard._domain import Domain
from blackboard._schemas import Observation

__all__ = ["Workbench", "Domain", "Observation"]
