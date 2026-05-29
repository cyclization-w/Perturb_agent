"""Background job runner for async workbench execution."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Job:
    def __init__(self, job_id: str, run_fn):
        self.job_id = job_id
        self._run_fn = run_fn
        self.status = "queued"  # queued → running → succeeded / failed / cancelled
        self.created_at = _now()
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.result: dict | None = None
        self.error: str | None = None
        self._cancel = threading.Event()

    def run(self):
        self.status = "running"
        self.started_at = _now()
        try:
            self.result = self._run_fn(self._cancel)
            self.status = "succeeded" if not self._cancel.is_set() else "cancelled"
        except Exception as exc:
            self.status = "failed"
            self.error = str(exc)
        self.finished_at = _now()

    def cancel(self):
        self._cancel.set()

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id, "status": self.status,
            "created_at": self.created_at, "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result, "error": self.error,
        }


class JobRunner:
    """In-process background job queue."""

    def __init__(self, max_workers: int = 2):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(max_workers)

    def submit(self, run_fn) -> Job:
        job = Job(f"job_{uuid4().hex[:10]}", run_fn)
        with self._lock:
            self._jobs[job.job_id] = job
        thread = threading.Thread(target=self._execute, args=(job,), daemon=True)
        thread.start()
        return job

    def _execute(self, job: Job):
        self._semaphore.acquire()
        try:
            if not job._cancel.is_set():
                job.run()
        finally:
            self._semaphore.release()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [j.to_dict() for j in self._jobs.values()]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
        if job and job.status in ("queued", "running"):
            job.cancel()
            return True
        return False
