"""Perturb-seq reference domain."""

from blackboard._domain import Domain

DOMAIN = Domain(
    name="perturbseq",
    agenda=[
        "experimental_design", "scrna_qc", "guide_assignment",
        "perturbation_validation", "target_qc", "state_reference",
        "effect_exploration", "target_discovery", "biology_story", "report",
    ],
    capabilities=[
        {"id": "perturbseq.experimental_design", "stage": "experimental_design",
         "description": "Audit perturbation modality, guide capture, controls, MOI, loading strategy."},
        {"id": "perturbseq.scrna_qc", "stage": "scrna_qc",
         "description": "Inspect UMI, feature, mitochondrial, empty droplet distributions."},
        {"id": "perturbseq.guide_assignment", "stage": "guide_assignment",
         "description": "Inspect guide columns, target mapping, guide counts, assignment thresholds."},
        {"id": "perturbseq.perturbation_validation", "stage": "perturbation_validation",
         "description": "Validate perturbation effect through target expression direction or gene signatures."},
        {"id": "perturbseq.target_qc", "stage": "target_qc",
         "description": "Check target coverage, cells per target, guide concordance."},
        {"id": "perturbseq.state_reference", "stage": "state_reference",
         "description": "Build or audit reference space, clustering, annotation, gene modules."},
        {"id": "perturbseq.effect_exploration", "stage": "effect_exploration",
         "description": "Explore global effect size, composition shifts, module effects, trajectory bias."},
        {"id": "perturbseq.target_discovery", "stage": "target_discovery",
         "description": "Rank co-functional and driver targets, draft regulatory hypotheses."},
        {"id": "perturbseq.biology_story", "stage": "biology_story",
         "description": "Draft cautious biological story candidates after data and method checks."},
        {"id": "perturbseq.report", "stage": "report",
         "description": "Assemble conclusions, limitations, artifacts, and derivation paths."},
    ],
    tools=[
        "scanpy", "anndata", "pertpy", "decoupler",
        "sklearn", "statsmodels", "matplotlib", "seaborn", "pandas", "numpy", "scipy",
    ],
    rubric=[
        "Check Perturb-seq experimental design before interpretation: modality, guide capture, controls, loading, MOI.",
        "For droplet overloading designs, avoid ordinary doublet filtering without a deconvolution plan.",
        "For guide assignment, treat low-MOI and high-MOI designs differently.",
        "Validate perturbation effects through target expression direction or gene signatures before target-level interpretation.",
        "For target-level claims, inspect target coverage and guide concordance before aggregation.",
        "Build state reference and gene modules before interpreting state composition or trajectory shifts.",
        "For target discovery, distinguish co-functional similarity, driver ranking, and regulatory-network hypotheses.",
        "Prefer autonomous validator checks for batch-condition confounding before asking the user.",
        "Treat low target coverage, guide discordance, empty DE, and bad plots as recoverable analysis issues first.",
        "Use web research only for biology story or follow-up hypotheses.",
    ],
    validators=[
        "control_label_audit", "batch_condition_crosstab", "guide_target_mapping_check",
        "target_coverage_check", "guide_concordance_check", "plot_artifact_check",
        "perturbation_modality_audit", "guide_capture_audit", "moi_loading_audit",
    ],
    protocol="""0. EXPERIMENTAL DESIGN AUDIT (do this first!)
   - Identify perturbation modality: KO, CRISPRi, CRISPRa
   - Check guide capture method, control design (NTC, positive control)
   - Assess loading strategy (droplet overloading vs normal) and MOI (low vs high)
   - DO NOT apply standard doublet filtering for overloading designs

1. DATA QC
   1.1 Standard scRNA-seq QC
       - Filter low UMI, low feature, high mito cells
       - Remove empty droplets
       - Register observations: n_cells, n_genes, median_UMI, pct_mito
   1.2 Guide assignment
       - Identify guide/gRNA column in .obs
       - Count guides per cell, compare assignment thresholds
       - Low-MOI vs high-MOI: different strategies
       - Register observations: n_cells_with_guide, n_guides_per_cell, assignment_rate
   1.3 Perturbation validation
       - Check target expression direction (KO = down, CRISPRa = up)
       - Validate via gene signatures if available
       - Register observations: logFC, p_value per target
   1.4 Target-level QC
       - Per-target cell/droplet count
       - Guide concordance within each target
       - Register observations: cells_per_target, guide_concordance

2. STATE REFERENCE
   2.1 Build reference space: PCA, UMAP, clustering, annotation
   2.2 Define gene modules from external knowledge or data-driven
   2.3 Register observations: n_clusters, cluster_labels, module_genes

3. PERTURBATION EFFECT EXPLORATION
   3.1 Global effect: overall distribution shift vs NTC
   3.2 Composition/state shifts: cluster abundance changes, module score changes
   3.3 Trajectory/fate bias: pseudotime, lineage shifts
   3.4 Co-regulated modules: genes with similar perturbation response patterns
   3.5 Register observations: effect_size, composition_shift, module_score_change

4. TARGET DISCOVERY
   4.1 Co-functional targets: similar transcriptional response profiles
   4.2 Driver targets: strong effect, affects key programs
   4.3 Regulatory network: TF modules → gene programs (e.g., Zhou et al.)
   4.4 Register observations: target_similarity, driver_score, network_edge

5. REPORT — Summarize key findings, evidence quality, limitations""",
)
