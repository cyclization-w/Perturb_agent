"""Code execution sandbox — subprocess (default) and Docker isolation."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run_code(code: str, workspace: str, artifacts_dir: str, *, backend: str = "subprocess", docker_image: str = "") -> dict:
    """Execute Python code in the selected sandbox."""
    if backend == "docker":
        return _run_docker(code, workspace, artifacts_dir, docker_image)
    return _run_subprocess(code, workspace, artifacts_dir)


def _run_subprocess(code: str, workspace: str, artifacts_dir: str) -> dict:
    script = Path(tempfile.mktemp(suffix=".py"))
    script.write_text(code, encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=120,
            cwd=workspace,
            env={**os.environ, "WORKSPACE": workspace, "ARTIFACTS_DIR": artifacts_dir},
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[-3000:] if result.stdout else "",
            "stderr": result.stderr[-3000:] if result.stderr else "",
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "stdout": "", "stderr": "Timed out (120s)", "timed_out": True}


def _run_docker(code: str, workspace: str, artifacts_dir: str, image: str) -> dict:
    image = image or os.getenv("BLACKBOARD_DOCKER_IMAGE", "python:3.11-slim")
    sandbox_dir = Path(artifacts_dir) / ".sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    script = sandbox_dir / "run.py"
    # Rewrite host paths in code to container paths
    adapted = code.replace(workspace, "/workspace").replace(artifacts_dir, "/artifacts")
    script.write_text(adapted, encoding="utf-8")

    cmd = [
        "docker", "run", "--rm",
        "--network", "none",
        "--memory", "2g",
        "--cpus", "2",
        "--timeout", "120",
        "-v", f"{workspace}:/workspace:ro",
        "-v", f"{artifacts_dir}:/artifacts",
        "-w", "/workspace",
        image,
        "python", "/artifacts/.sandbox/run.py",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=130)
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[-3000:] if result.stdout else "",
            "stderr": result.stderr[-3000:] if result.stderr else "",
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "stdout": "", "stderr": "Docker execution timed out (120s)", "timed_out": True}
    except FileNotFoundError:
        return {"returncode": 127, "stdout": "", "stderr": "Docker not found. Install Docker or use backend='subprocess'.", "timed_out": False}
    except OSError as exc:
        return {"returncode": 127, "stdout": "", "stderr": str(exc), "timed_out": False}
