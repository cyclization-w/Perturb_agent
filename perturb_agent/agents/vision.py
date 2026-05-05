from __future__ import annotations

import asyncio
import base64
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from perturb_agent.schemas.artifacts import Artifact
from perturb_agent.schemas.review import FigureReview


class VisionLLMClient:
    """Provider-agnostic interface for future small vision model integrations."""

    async def review_figure(self, artifact: Artifact, context: dict) -> FigureReview:
        raise NotImplementedError


class NoopVisionReviewer(VisionLLMClient):
    async def review_figure(self, artifact: Artifact, context: dict) -> FigureReview:
        path = Path(artifact.path)
        return FigureReview(
            artifact_id=artifact.id,
            is_valid=path.exists() and path.stat().st_size > 0,
            quality="unknown",
            observations=["No vision provider configured; checked that the file exists."],
            confidence=0.1,
        )


class OpenAICompatibleVisionReviewer(VisionLLMClient):
    """Minimal OpenAI-compatible VLM client.

    Many commercial VLM APIs expose a Chat Completions-compatible endpoint. Keep
    this adapter small and provider-neutral; the rest of the runtime only depends
    on the FigureReview schema.

    Required env vars:
      VISION_BASE_URL=https://provider.example.com/v1
      VISION_API_KEY=...
      VISION_MODEL=...
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60,
    ) -> None:
        self.base_url = (base_url or os.getenv("VISION_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("VISION_API_KEY", "")
        self.model = model or os.getenv("VISION_MODEL", "")
        self.timeout = timeout
        if not self.base_url or not self.api_key or not self.model:
            raise ValueError("VISION_BASE_URL, VISION_API_KEY, and VISION_MODEL are required.")

    async def review_figure(self, artifact: Artifact, context: dict) -> FigureReview:
        return await asyncio.to_thread(self._review_figure_sync, artifact, context)

    def _review_figure_sync(self, artifact: Artifact, context: dict) -> FigureReview:
        image_path = Path(artifact.path)
        image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a concise scientific figure reviewer. Return only JSON "
                        "matching: {is_valid:boolean, quality:string, issues:string[], "
                        "observations:string[], suggested_actions:string[], confidence:number}."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Review this generated analysis figure for emptiness, layout "
                                f"problems, obvious plotting issues, and scientific readability. "
                                f"Context: {json.dumps(context, ensure_ascii=False)}"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{artifact.mime_type or 'image/png'};base64,{image_b64}"
                            },
                        },
                    ],
                },
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))

        content = raw["choices"][0]["message"]["content"]
        parsed = self._parse_json_content(content)
        quality = str(parsed.get("quality", "unknown")).lower()
        if quality not in {"good", "acceptable", "poor", "unknown"}:
            quality = "unknown"
        return FigureReview(
            artifact_id=artifact.id,
            is_valid=bool(parsed.get("is_valid", False)),
            quality=quality,
            issues=list(parsed.get("issues", [])),
            observations=list(parsed.get("observations", [])),
            suggested_actions=list(parsed.get("suggested_actions", [])),
            confidence=float(parsed.get("confidence", 0.0)),
        )

    def _parse_json_content(self, content: str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(content, dict):
            return content
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                return json.loads(content[start : end + 1])
            raise
