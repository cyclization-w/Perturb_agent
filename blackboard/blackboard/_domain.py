"""Domain pack contract — the only extension point."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Domain(BaseModel):
    """Define a domain for the workbench.

    A domain pack tells the LLM what analysis stages exist, what tools
    are available, what validators to run, and what rubric to use when
    reviewing results.

    Example:
        Domain(
            name="climate",
            agenda=["data_load", "bias_correction", "trend", "report"],
            capabilities=[
                {"id": "climate.load", "stage": "data_load",
                 "description": "Load NetCDF/GRIB files and inspect variables."},
            ],
            tools=["xarray", "scipy", "matplotlib"],
            rubric=["Check spatial resolution before trend analysis."],
        )
    """

    name: str
    agenda: list[str] = Field(default_factory=list)
    capabilities: list[dict] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    rubric: list[str] = Field(default_factory=list)
    validators: list[str] = Field(default_factory=list)
    protocol: str = ""  # Detailed step-by-step analysis procedure — injected into planner prompt
    audit_preamble: str = ""
