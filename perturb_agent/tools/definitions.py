from __future__ import annotations


TOOL_DEFINITIONS = [
    {
        "name": "query_state",
        "description": "Read the persisted analysis_state. Use this before non-trivial decisions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "Optional section: dataset, experimental_design, pipeline, decisions, open_issues, assumptions, pi_preferences, next_actions, artifacts, or all.",
                }
            },
        },
    },
    {
        "name": "inspect_anndata",
        "description": (
            "Inspect an AnnData .h5ad file with a standardized Jupyter cell, summarize its "
            "shape/metadata/embeddings/layers, infer likely biological columns, and update "
            "analysis_state. Use this as the first step after receiving a new .h5ad path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the .h5ad AnnData file to inspect.",
                },
                "dataset_id": {
                    "type": "string",
                    "description": "Optional short dataset identifier to store in analysis_state.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "propose_decision",
        "description": (
            "Propose a non-trivial analysis or workflow decision. Every proposal must include "
            "tier, tier_reason, reversibility, expected_outcome, and whether PI confirmation is required."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string"},
                "stage": {"type": "string"},
                "tier": {"type": "string", "enum": ["A", "B", "C"]},
                "tier_reason": {"type": "string"},
                "requires_pi": {"type": "boolean"},
                "reversibility": {"type": "string", "enum": ["trivial", "moderate", "expensive"]},
                "decision": {"type": "object"},
                "reasoning": {"type": "string"},
                "expected_outcome": {"type": "string"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                "alternatives_considered": {"type": "array", "items": {"type": "object"}},
            },
            "required": [
                "decision_id",
                "stage",
                "tier",
                "tier_reason",
                "requires_pi",
                "reversibility",
                "decision",
                "reasoning",
                "expected_outcome",
            ],
        },
    },
    {
        "name": "commit_decision",
        "description": "Commit a previously proposed decision into analysis_state if tier rules allow it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string"},
                "pi_confirmation_ref": {"type": "string"},
                "modifications_from_proposal": {"type": "object"},
            },
            "required": ["decision_id"],
        },
    },
    {
        "name": "execute_python",
        "description": (
            "Execute Python code inside the persistent Jupyter kernel. Returns stdout, stderr, "
            "traceback, and generated artifact paths."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "request_pi_input",
        "description": (
            "Ask the PI/user a blocking or important question. Required for Tier C decisions "
            "and unresolved experimental-design unknowns. Provide context, options, and your "
            "recommendation so the PI can answer efficiently."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "context_summary": {"type": "string"},
                "options": {"type": "array", "items": {"type": "object"}},
                "your_recommendation": {"type": "string"},
                "urgency": {"type": "string", "enum": ["blocking", "soon", "whenever"]},
            },
            "required": ["question", "context_summary", "options", "your_recommendation", "urgency"],
        },
    },
    {
        "name": "finish",
        "description": "Finish the current analysis task with a concise summary.",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
]
