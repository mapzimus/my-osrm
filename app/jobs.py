"""Background jobs for slow work (isochrones take minutes, the report card ~30 s).

    job = jobs.submit("isochrone", fn)          # fn(job) runs on a worker thread
    job.update(0.4, "214/500 requests")        # from inside fn
    job.check_cancelled()                      # raises JobCancelled
    return value becomes job.result

Endpoints (mounted by main.py): GET /api/jobs/{id}, GET /api/jobs/{id}/events (SSE),
DELETE /api/jobs/{id} (cancel).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse


class JobCancelled(Exception):
    pass


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"            # queued | running | done | failed | cancelled
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    done_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def update(self, progress: float | None = None, message: str | None = None) -> None:
        if progress is not None:
            self.progress = max(0.0, min(1.0, float(progress)))
        if message is not None:
            self.message = message
        self.check_cancelled()

    def check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled()

    @property
    def terminal(self) -> bool:
        return self.status in ("done", "failed", "cancelled")

    def public(self, with_result: bool = False) -> dict:
        d = {"id": self.id, "kind": self.kind, "status": self.status, "progress": round(self.progress, 4),
             "message": self.message, "error": self.error, "created": self.created, "finished": self.finished}
        if with_result:
            d["result"] = self.result
        return d


class JobManager:
    def __init__(self, workers: int = 2, keep: int = 20) -> None:
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self.jobs: dict[str, Job] = {}
        self.keep = keep
        self.lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[Job], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        with self.lock:
            self.jobs[job.id] = job
            self._evict()
        self.pool.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn: Callable[[Job], Any]) -> None:
        job.status = "running"
        try:
            job.check_cancelled()
            job.result = fn(job)
            job.status, job.progress = "done", 1.0
        except JobCancelled:
            job.status = "cancelled"
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            job.status = "failed"
            job.error = f"{exc.__class__.__name__}: {exc}"
            traceback.print_exc()
        finally:
            job.finished = time.time()
            job.done_event.set()

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"no job {job_id}")
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        job._cancel.set()
        return job

    def _evict(self) -> None:
        finished = [j for j in self.jobs.values() if j.terminal]
        for j in sorted(finished, key=lambda j: j.finished or 0)[: max(0, len(finished) - self.keep)]:
            del self.jobs[j.id]


manager = JobManager()
router = APIRouter()


@router.get("/{job_id}")
def job_status(job_id: str, result: bool = False) -> dict:
    return manager.get(job_id).public(with_result=result)


@router.delete("/{job_id}")
def job_cancel(job_id: str) -> dict:
    return manager.cancel(job_id).public()


@router.get("/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = manager.get(job_id)

    async def gen():
        last = None
        while True:
            snap = job.public()
            if snap != last:
                yield f"data: {json.dumps(snap)}\n\n"
                last = snap
            if job.terminal:
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
