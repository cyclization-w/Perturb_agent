from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jupyter_core.paths
from jupyter_client.manager import AsyncKernelManager

from perturb_agent.kernel.artifacts import ArtifactManager
from perturb_agent.schemas.execution import CellExecutionResult, CellOutput


class KernelSession:
    def __init__(self, figures_dir: Path, runtime_dir: Path, kernel_name: str = "python3") -> None:
        self.kernel_name = kernel_name
        self.runtime_dir = runtime_dir
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.km = AsyncKernelManager(
            kernel_name=kernel_name,
            connection_file=str(self.runtime_dir / "kernel-connection.json"),
        )
        self.kc = None
        self.artifacts = ArtifactManager(figures_dir)

    async def start(self) -> None:
        # Some Windows environments reject Jupyter's DACL tightening for connection
        # files. The run directory is local to this project, so insecure writes are
        # acceptable for this MVP controller process.
        os.environ.setdefault("JUPYTER_ALLOW_INSECURE_WRITES", "1")
        jupyter_core.paths.allow_insecure_writes = True
        env = os.environ.copy()
        env["JUPYTER_ALLOW_INSECURE_WRITES"] = "1"
        env["IPYTHONDIR"] = str(self.runtime_dir / "ipython")
        env["JUPYTER_CONFIG_DIR"] = str(self.runtime_dir / "jupyter_config")
        env["PYDEVD_DISABLE_FILE_VALIDATION"] = "1"
        Path(env["IPYTHONDIR"]).mkdir(parents=True, exist_ok=True)
        Path(env["JUPYTER_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)
        await self.km.start_kernel(env=env)
        self.kc = self.km.client()
        self.kc.start_channels()
        await self.kc.wait_for_ready(timeout=30)

    async def is_alive(self) -> bool:
        return await self.km.is_alive()

    async def execute(self, cell_id: str, code: str, timeout: float = 300) -> CellExecutionResult:
        if self.kc is None:
            raise RuntimeError("Kernel client is not started.")

        started_at = datetime.now(timezone.utc)
        msg_id = self.kc.execute(code)
        outputs: list[CellOutput] = []
        artifacts = []
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        traceback: list[str] = []
        status = "success"
        execution_count = None
        output_index = 0

        try:
            while True:
                msg = await asyncio.wait_for(self.kc.get_iopub_msg(), timeout=timeout)
                parent = msg.get("parent_header", {})
                if parent.get("msg_id") != msg_id:
                    continue

                msg_type = msg["header"]["msg_type"]
                content: dict[str, Any] = msg["content"]

                if msg_type == "stream":
                    text = content.get("text", "")
                    name = content.get("name", "stdout")
                    outputs.append(CellOutput(output_type="stream", name=name, text=text))
                    if name == "stderr":
                        stderr_parts.append(text)
                    else:
                        stdout_parts.append(text)

                elif msg_type == "execute_result":
                    execution_count = content.get("execution_count", execution_count)
                    outputs.append(
                        CellOutput(
                            output_type="execute_result",
                            data=content.get("data", {}),
                        )
                    )

                elif msg_type == "display_data":
                    data = content.get("data", {})
                    outputs.append(CellOutput(output_type="display_data", data=data))
                    artifacts.extend(
                        self.artifacts.save_display_artifacts(
                            cell_id=cell_id,
                            output_index=output_index,
                            data=data,
                        )
                    )
                    output_index += 1

                elif msg_type == "error":
                    status = "error"
                    traceback = content.get("traceback", [])
                    outputs.append(
                        CellOutput(
                            output_type="error",
                            ename=content.get("ename"),
                            evalue=content.get("evalue"),
                            traceback=traceback,
                        )
                    )

                elif msg_type == "execute_input":
                    execution_count = content.get("execution_count", execution_count)

                elif msg_type == "status" and content.get("execution_state") == "idle":
                    break

        except asyncio.TimeoutError:
            status = "timeout"
            await self.interrupt()

        return CellExecutionResult(
            cell_id=cell_id,
            code=code,
            status=status,
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            traceback=traceback,
            outputs=outputs,
            artifacts=artifacts,
            execution_count=execution_count,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )

    async def interrupt(self) -> None:
        await self.km.interrupt_kernel()

    async def shutdown(self) -> None:
        if self.kc is not None:
            self.kc.stop_channels()
        if await self.km.is_alive():
            await self.km.shutdown_kernel(now=True)
