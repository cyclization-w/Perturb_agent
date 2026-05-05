from __future__ import annotations

import json
from textwrap import dedent

from perturb_agent.kernel.bootstrap import build_bootstrap_code
from perturb_agent.kernel.session import KernelSession
from perturb_agent.runtime.context import RuntimeContext
from perturb_agent.runtime.run_manager import RunManager
from perturb_agent.schemas.base import model_to_dict, model_to_json
from perturb_agent.tools.formatting import format_run_code_result
from perturb_agent.tools.schemas import RunCodeInput, validate_model


class ToolHandlers:
    def __init__(
        self,
        *,
        kernel: KernelSession,
        run_manager: RunManager,
        runtime: RuntimeContext,
    ) -> None:
        self.kernel = kernel
        self.run_manager = run_manager
        self.runtime = runtime

    async def run_code(self, tool_input: dict) -> dict:
        parsed = validate_model(RunCodeInput, tool_input)
        await self._ensure_kernel_ready()

        self.runtime.cell_counter += 1
        cell_id = f"cell_{self.runtime.cell_counter:04d}"
        self.run_manager.event_log.write(
            "tool_run_code_started",
            {"cell_id": cell_id, "title": parsed.title},
        )
        result = await self.kernel.execute(cell_id=cell_id, code=parsed.code)
        self.run_manager.notebook.append_result(result)
        self.run_manager.event_log.write("cell_finished", model_to_dict(result))
        formatted = format_run_code_result(result, str(self.run_manager.notebook_path))
        return {
            "type": "tool_result",
            "content": model_to_json(formatted),
        }

    async def inspect_anndata(self, tool_input: dict) -> dict:
        path = str(tool_input["path"])
        dataset_id = tool_input.get("dataset_id")
        await self._ensure_kernel_ready()

        self.runtime.cell_counter += 1
        cell_id = f"cell_{self.runtime.cell_counter:04d}"
        self.run_manager.event_log.write(
            "tool_inspect_anndata_started",
            {"cell_id": cell_id, "path": path, "dataset_id": dataset_id},
        )
        result = await self.kernel.execute(cell_id=cell_id, code=_build_inspect_anndata_code(path, dataset_id))
        self.run_manager.notebook.append_result(result)
        self.run_manager.event_log.write("cell_finished", model_to_dict(result))

        summary = _extract_inspection_summary(result.stdout)
        formatted = format_run_code_result(result, str(self.run_manager.notebook_path))
        payload = {
            "inspection": summary,
            "execution": model_to_dict(formatted),
        }
        return {
            "type": "tool_result",
            "content": json.dumps(payload, ensure_ascii=False, default=str),
        }

    async def _ensure_kernel_ready(self) -> None:
        if not self.runtime.kernel_started or not await self.kernel.is_alive():
            await self.kernel.start()
            self.runtime.kernel_started = True
            self.runtime.bootstrap_done = False
            self.run_manager.event_log.write("kernel_started", {"kernel_name": self.kernel.kernel_name})

        if not self.runtime.bootstrap_done:
            self.runtime.cell_counter += 1
            cell_id = f"cell_{self.runtime.cell_counter:04d}"
            code = build_bootstrap_code(self.runtime)
            result = await self.kernel.execute(cell_id=cell_id, code=code)
            self.run_manager.notebook.append_result(result)
            self.run_manager.event_log.write("kernel_bootstrapped", model_to_dict(result))
            self.runtime.bootstrap_done = result.status == "success"


def _build_inspect_anndata_code(path: str, dataset_id: str | None) -> str:
    return dedent(
        f"""
        import json
        import re
        from pathlib import Path

        import scanpy as sc

        adata_path = Path({path!r}).expanduser()
        adata = sc.read_h5ad(str(adata_path))

        def _keys(mapping):
            return [str(k) for k in mapping.keys()]

        def _safe_unique(series, limit=20):
            try:
                values = series.dropna().astype(str).value_counts().head(limit)
                return values.to_dict()
            except Exception:
                return {{}}

        def _match_columns(columns, patterns):
            matches = []
            for col in columns:
                lowered = str(col).lower()
                if any(re.search(pattern, lowered) for pattern in patterns):
                    matches.append(str(col))
            return matches

        obs_columns = [str(c) for c in adata.obs.columns]
        var_columns = [str(c) for c in adata.var.columns]
        uns_keys = _keys(adata.uns)
        obsm_keys = _keys(adata.obsm)
        varm_keys = _keys(adata.varm)
        layer_keys = _keys(adata.layers)

        candidate_patterns = {{
            "cell_type": [r"cell.?type", r"celltype", r"annotation", r"cluster", r"leiden", r"louvain"],
            "batch": [r"batch", r"sample", r"library", r"donor", r"patient", r"replicate"],
            "condition": [r"condition", r"treatment", r"stim", r"status", r"disease", r"group"],
            "perturbation": [r"perturb", r"condition", r"treatment", r"target", r"gene", r"knock", r"crispr"],
            "guide": [r"guide", r"grna", r"sgRNA".lower(), r"barcode"],
            "target": [r"target", r"gene", r"perturb"],
            "control": [r"control", r"ntc", r"non.?target", r"mock", r"vehicle", r"untreated"],
            "timepoint": [r"time", r"hour", r"day"],
        }}
        candidates = {{
            name: _match_columns(obs_columns, patterns)
            for name, patterns in candidate_patterns.items()
        }}

        embedding_status = {{
            "has_umap": "X_umap" in obsm_keys,
            "has_pca": "X_pca" in obsm_keys,
            "has_neighbors": "neighbors" in uns_keys,
        }}

        preview_columns = []
        for names in candidates.values():
            preview_columns.extend(names[:2])
        preview_columns = list(dict.fromkeys(preview_columns))[:12]
        obs_value_preview = {{
            col: _safe_unique(adata.obs[col])
            for col in preview_columns
            if col in adata.obs.columns
        }}

        guide_hits = candidates["guide"]
        perturb_hits = [c for c in candidates["perturbation"] if c not in candidates["condition"][:1]]
        control_labels = []
        for col in set(candidates["control"] + candidates["condition"] + candidates["perturbation"]):
            if col in adata.obs.columns:
                for value in adata.obs[col].dropna().astype(str).unique()[:200]:
                    lowered = value.lower()
                    if any(token in lowered for token in ["ntc", "non-target", "nontarget", "control", "mock", "vehicle", "untreated"]):
                        control_labels.append({{"column": str(col), "label": str(value)}})

        perturbseq_score = 0
        if guide_hits:
            perturbseq_score += 2
        if perturb_hits:
            perturbseq_score += 1
        if control_labels:
            perturbseq_score += 1
        if any("perturb" in key.lower() or "guide" in key.lower() for key in uns_keys):
            perturbseq_score += 1

        if perturbseq_score >= 3:
            perturbseq_likelihood = "high"
        elif perturbseq_score >= 1:
            perturbseq_likelihood = "medium"
        else:
            perturbseq_likelihood = "low"

        missing_design_fields = []
        if perturbseq_likelihood != "low":
            if not guide_hits:
                missing_design_fields.append("guide_column")
            if not perturb_hits:
                missing_design_fields.append("perturbation_or_target_column")
            if not control_labels:
                missing_design_fields.append("control_definition")
        else:
            missing_design_fields.append("whether_to_treat_as_perturbseq")
        missing_design_fields.extend(["perturbation_type", "guide_capture", "moi_design", "loading_strategy"])

        recommended_pi_questions = []
        if perturbseq_likelihood == "low":
            recommended_pi_questions.append(
                "I do not see obvious guide or perturbation columns. Should I treat this as ordinary scRNA-seq rather than Perturb-seq?"
            )
        else:
            if not guide_hits:
                recommended_pi_questions.append("Which obs column contains guide or gRNA assignment?")
            if not control_labels:
                recommended_pi_questions.append("Which labels should be treated as NTC/non-targeting or other controls?")
            recommended_pi_questions.append("Is this low-MOI or high-MOI, and should multiple-guide cells be kept?")

        summary = {{
            "path": str(adata_path),
            "dataset_id": {dataset_id!r},
            "shape": {{"n_cells": int(adata.n_obs), "n_genes": int(adata.n_vars)}},
            "obs_columns": obs_columns,
            "var_columns": var_columns,
            "uns_keys": uns_keys,
            "obsm_keys": obsm_keys,
            "varm_keys": varm_keys,
            "layers": layer_keys,
            "candidate_columns": candidates,
            "control_label_candidates": control_labels[:20],
            "obs_value_preview": obs_value_preview,
            "embedding_status": embedding_status,
            "dataset_likelihood": {{
                "perturbseq": perturbseq_likelihood,
                "ordinary_scrna": "high" if perturbseq_likelihood == "low" else "uncertain",
            }},
            "missing_design_fields": list(dict.fromkeys(missing_design_fields)),
            "recommended_pi_questions": recommended_pi_questions,
        }}

        print("ANNDATA_INSPECTION_JSON_START")
        print(json.dumps(summary, ensure_ascii=False, default=str))
        print("ANNDATA_INSPECTION_JSON_END")
        """
    ).strip()


def _extract_inspection_summary(stdout: str) -> dict:
    start = "ANNDATA_INSPECTION_JSON_START"
    end = "ANNDATA_INSPECTION_JSON_END"
    if start not in stdout or end not in stdout:
        return {"error": "Inspection JSON markers were not found in stdout.", "stdout": stdout[-4000:]}
    payload = stdout.split(start, 1)[1].split(end, 1)[0].strip()
    return json.loads(payload)
