"""Agent tools — read-only functions the LLM can call during analysis.

All tools return plain dicts. The LLM receives tool results and decides
what to do next — there is no prescribed order or pipeline stage.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path


def _vlm_config():
    """VLM provider can differ from the main LLM provider."""
    return {
        "provider": os.getenv("BLACKBOARD_VLM_PROVIDER", ""),
        "api_key": os.getenv("BLACKBOARD_VLM_API_KEY", ""),
        "base_url": os.getenv("BLACKBOARD_VLM_BASE_URL", ""),
        "model": os.getenv("BLACKBOARD_VLM_MODEL", "gpt-4o"),
    }


def view_plot(path: str) -> dict:
    """Read an image file and describe it using a VLM.

    If no VLM is configured, returns file metadata only.
    Configure with:
      BLACKBOARD_VLM_API_KEY=sk-...
      BLACKBOARD_VLM_MODEL=gpt-4o
      BLACKBOARD_VLM_BASE_URL=https://api.openai.com/v1  (optional)
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"File not found: {path}"}
    vlm = _vlm_config()
    if not vlm["api_key"]:
        return {"path": str(p), "size_bytes": p.stat().st_size,
                "note": "No VLM configured. Set BLACKBOARD_VLM_API_KEY to enable plot inspection."}

    try:
        data = base64.b64encode(p.read_bytes()).decode("ascii")
        ext = p.suffix.lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif"}.get(ext, "image/png")
        from openai import OpenAI
        client = OpenAI(api_key=vlm["api_key"], base_url=vlm["base_url"] or None)
        response = client.chat.completions.create(
            model=vlm["model"],
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "Describe this scientific plot in detail. What does it show? Any issues (empty, malformed, unexpected patterns)?"},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}", "detail": "high"}},
            ]}],
            max_tokens=500,
        )
        description = response.choices[0].message.content
        return {"path": str(p), "size_bytes": p.stat().st_size, "description": description}
    except Exception as exc:
        return {"path": str(p), "size_bytes": p.stat().st_size, "error": str(exc)}


def read_file(path: str, max_lines: int = 30) -> dict:
    """Read the first N lines of a text/CSV/JSON file.

    The LLM calls this to inspect output files without leaving the harness.
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"File not found: {path}"}
    try:
        text = p.read_text(encoding="utf-8")
        lines = text.split("\n")
        return {"path": str(p), "size_bytes": p.stat().st_size, "lines": len(lines),
                "preview": "\n".join(lines[:max_lines]), "suffix": "..." if len(lines) > max_lines else ""}
    except Exception as exc:
        return {"error": str(exc)}


def search_web(query: str) -> dict:
    """Search the web for scientific information (gene function, pathway, etc.).

    Requires OPENAI_API_KEY and a model that supports web_search_preview.
    Falls back gracefully if not available.
    """
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("OPENAI_BASE_URL") or None)
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            input=query,
            tools=[{"type": "web_search_preview"}],
            max_output_tokens=2000,
        )
        return {"query": query, "result": response.output_text[:3000]}
    except Exception as exc:
        return {"query": query, "error": str(exc), "note": "Web search requires OpenAI API with web_search_preview support."}


def query_observations(snap, target: str = "", metric: str = "", limit: int = 10) -> dict:
    """Query registered observations across all attempts and branches.

    The LLM calls this to check prior values before making a new plan.
    """
    obs_list = []
    for o in snap.observations:
        if target and o.target.lower() != target.lower():
            continue
        if metric and o.metric.lower() != metric.lower():
            continue
        obs_list.append({
            "target": o.target, "metric": o.metric, "value": o.value,
            "contrast": o.contrast, "method": o.method,
            "attempt_id": o.attempt_id, "branch_id": o.branch_id,
        })
    obs_list.sort(key=lambda x: x["attempt_id"])
    return {"query": {"target": target, "metric": metric}, "count": len(obs_list), "results": obs_list[-limit:]}


def list_artifacts(snap) -> dict:
    """List all artifacts produced so far."""
    return {"artifacts": [{"id": a.artifact_id, "kind": a.kind, "path": a.path, "summary": a.summary} for a in snap.artifacts]}


# ── Tool registry ────────────────────────────────────────────────────────

TOOLS = {
    "view_plot": {
        "fn": view_plot,
        "description": "View a generated plot/image. Call after generating a figure to inspect it visually.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to the image file"}},
            "required": ["path"],
        },
    },
    "read_file": {
        "fn": read_file,
        "description": "Read the first lines of a text/CSV/JSON output file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file"},
                "max_lines": {"type": "integer", "description": "Max lines to return (default 30)"},
            },
            "required": ["path"],
        },
    },
    "search_web": {
        "fn": search_web,
        "description": "Search the web for gene function, pathway info, or scientific context.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    },
}


def execute_tool(name: str, args: dict, snap=None) -> dict:
    """Execute a tool by name. snap is injected for tools that need it."""
    if name not in TOOLS:
        return {"error": f"Unknown tool: {name}"}
    fn = TOOLS[name]["fn"]
    try:
        if name in ("query_observations", "list_artifacts") and snap is not None:
            return fn(snap, **args)
        return fn(**args)
    except Exception as exc:
        return {"error": str(exc)}


def tool_schemas() -> list[dict]:
    """Return OpenAI-compatible tool schemas for all registered tools."""
    return [{
        "type": "function",
        "function": {
            "name": name,
            "description": spec["description"],
            "parameters": spec["parameters"],
        },
    } for name, spec in TOOLS.items()]
