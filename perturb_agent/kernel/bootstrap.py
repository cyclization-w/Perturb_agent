from __future__ import annotations

import json

from perturb_agent.runtime.context import RuntimeContext


def build_bootstrap_code(context: RuntimeContext) -> str:
    payload = {
        "PROJECT_ROOT": str(context.project_root),
        "RUN_DIR": str(context.run_dir),
        "ARTIFACTS_DIR": str(context.artifacts_dir),
        "NOTEBOOK_PATH": str(context.notebook_path),
    }
    return (
        "from pathlib import Path\n"
        "import os, json\n"
        f"_PERTURB_RUNTIME = {json.dumps(payload, ensure_ascii=False)!r}\n"
        "_PERTURB_RUNTIME = json.loads(_PERTURB_RUNTIME)\n"
        "PROJECT_ROOT = Path(_PERTURB_RUNTIME['PROJECT_ROOT'])\n"
        "RUN_DIR = Path(_PERTURB_RUNTIME['RUN_DIR'])\n"
        "ARTIFACTS_DIR = Path(_PERTURB_RUNTIME['ARTIFACTS_DIR'])\n"
        "NOTEBOOK_PATH = Path(_PERTURB_RUNTIME['NOTEBOOK_PATH'])\n"
        "os.chdir(PROJECT_ROOT)\n"
        "print('Jupyter runtime ready')\n"
        "print('PROJECT_ROOT =', PROJECT_ROOT)\n"
        "print('RUN_DIR =', RUN_DIR)\n"
        "print('ARTIFACTS_DIR =', ARTIFACTS_DIR)\n"
    )

