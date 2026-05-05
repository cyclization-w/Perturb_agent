from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FigureReview(BaseModel):
    artifact_id: str
    is_valid: bool
    quality: Literal["good", "acceptable", "poor", "unknown"] = "unknown"
    issues: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class ReviewDecision(BaseModel):
    continue_run: bool
    summary: str
    figure_reviews: list[FigureReview] = Field(default_factory=list)

