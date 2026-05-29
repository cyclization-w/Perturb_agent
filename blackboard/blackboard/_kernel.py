"""Persistent Jupyter kernel — like CellVoyager and legacy paper_v1.

Variables, imports, and loaded data carry across cells.
Each cell sees stdout/stderr/errors captured in full.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class KernelSession:
    """Synchronous wrapper around a persistent Jupyter kernel."""

    def __init__(self, workspace: str, artifacts_dir: str, domain_imports: list[str] | None = None):
        self.workspace = workspace
        self.artifacts_dir = artifacts_dir
        self.domain_imports = domain_imports or []
        self.km = None
        self.kc = None
        self._started = False

    def start(self):
        if self._started:
            return
        try:
            import jupyter_core.paths
            from jupyter_client.manager import KernelManager
        except ImportError:
            raise RuntimeError("Persistent kernel requires: pip install jupyter_client ipykernel")

        os.environ.setdefault("JUPYTER_ALLOW_INSECURE_WRITES", "1")
        jupyter_core.paths.allow_insecure_writes = True

        self.km = KernelManager(kernel_name="python3")
        self.km.start_kernel()
        self.kc = self.km.client()
        self.kc.start_channels()
        self.kc.wait_for_ready(timeout=30)

        # Bootstrap: inject paths, imports, and audit contract
        bootstrap = (
            f"import sys, os, json, atexit\n"
            f"from pathlib import Path\n"
            f"workspace = Path('{self.workspace}')\n"
            f"artifacts_dir = Path('{self.artifacts_dir}')\n"
            f"artifacts_dir.mkdir(parents=True, exist_ok=True)\n"
            f"os.chdir('{self.workspace}')\n"
            f"_manifest = {{'manifest_id': 'kernel', 'attempt_id': '', 'artifacts': [], 'observations': []}}\n"
            f"def register_artifact(path, kind, summary='', metadata=None):\n"
            f"    _manifest['artifacts'].append({{'path': str(path), 'kind': str(kind), 'summary': str(summary), 'metadata': dict(metadata or {{}})}})\n"
            f"def register_observation(type, target='', metric='', value=None, contrast='', method='', parameters=None, uncertainty=None):\n"
            f"    _manifest['observations'].append({{'type': str(type), 'target': str(target), 'metric': str(metric), 'value': value, 'contrast': str(contrast), 'method': str(method), 'parameters': dict(parameters or {{}}), 'uncertainty': dict(uncertainty or {{}})}})\n"
            f"def _flush_manifest():\n"
            f"    p = artifacts_dir / (_manifest.get('attempt_id', 'kernel') + '_manifest.json')\n"
            f"    p.write_text(json.dumps(_manifest, ensure_ascii=False, indent=2, default=str), encoding='utf-8')\n"
            f"    print(f'MANIFEST: {{p}}', file=sys.stderr)\n"
            f"atexit.register(_flush_manifest)\n"
            f"print('Kernel ready. workspace:', workspace, file=sys.stderr)\n"
        )
        self._execute_sync(bootstrap)
        self._started = True

    def execute(self, attempt_id: str, code: str, timeout: float = 120) -> dict:
        """Execute code in the kernel. Returns {stdout, stderr, returncode, ...}."""
        self.start()
        # Set attempt_id for manifest
        self._execute_sync(f"_manifest['attempt_id'] = '{attempt_id}'")
        result = self._execute_sync(code, timeout=timeout)
        return result

    def _execute_sync(self, code: str, timeout: float = 30) -> dict:
        """Execute synchronously using the Jupyter kernel."""
        if self.kc is None:
            return {"returncode": 1, "stdout": "", "stderr": "Kernel not started."}

        msg_id = self.kc.execute(code)
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        traceback: list[str] = []
        status = "success"

        import time
        deadline = time.time() + timeout
        try:
            while True:
                if time.time() > deadline:
                    status = "timeout"
                    break
                try:
                    msg = self.kc.get_iopub_msg(timeout=1)
                except Exception:
                    continue

                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue

                msg_type = msg["header"]["msg_type"]
                content = msg["content"]

                if msg_type == "stream":
                    text = content.get("text", "")
                    if content.get("name") == "stderr":
                        stderr_parts.append(text)
                    else:
                        stdout_parts.append(text)

                elif msg_type == "error":
                    status = "error"
                    traceback = content.get("traceback", [])

                elif msg_type == "status" and content.get("execution_state") == "idle":
                    break

        except Exception as exc:
            status = "error"
            stderr_parts.insert(0, f"Kernel error: {exc}\n")

        return {
            "returncode": 0 if status == "success" else 1,
            "stdout": "".join(stdout_parts),
            "stderr": "".join(stderr_parts + traceback),
            "timed_out": status == "timeout",
        }

    def restart(self):
        """Restart the kernel, preserving bootstrap."""
        if self.kc:
            self.kc.stop_channels()
        if self.km:
            if self.km.is_alive():
                self.km.shutdown_kernel(now=True)
        self._started = False
        self.start()

    def shutdown(self):
        if self.kc:
            self.kc.stop_channels()
        if self.km and self.km.is_alive():
            self.km.shutdown_kernel(now=True)
        self._started = False

    def alive(self) -> bool:
        return self.km is not None and self.km.is_alive()
