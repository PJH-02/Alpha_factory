"""Cooperative persistent single-worker polling for durable jobs."""

from __future__ import annotations

import re
from threading import Event, Lock

from alpha_foundry.application.jobs import JobStateError
from alpha_foundry.application.ports import JobExecutor, JobRepository
from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail
from alpha_foundry.domain.models import Job, JobStatus

_RESULT_REFERENCE = re.compile(r"cas://sha256/[0-9a-f]{64}")


class PersistentWorker:
    """Claim and execute one job at a time with explicit cancellation boundaries.

    Cancellation may win while a handler is executing.  The worker never publishes a
    success or failure over that terminal cancellation; it observes cancellation before
    dispatch and after the handler's external-work boundary instead.
    """

    def __init__(
        self,
        repository: JobRepository,
        executor: JobExecutor,
        *,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._repository = repository
        self._executor = executor
        self._poll_interval_seconds = poll_interval_seconds
        self._started = False
        self._startup_lock = Lock()
        self._run_lock = Lock()

    def startup(self) -> tuple[Job, ...]:
        """Fail every job persisted as RUNNING before any queue claim occurs."""

        with self._startup_lock:
            if self._started:
                return ()
            recovered = self._repository.recover_interrupted_jobs()
            self._started = True
            return recovered

    def run_once(self) -> Job | None:
        """Claim at most one queued job and complete it at a safe lifecycle boundary."""

        with self._run_lock:
            return self._run_once()

    def _run_once(self) -> Job | None:
        self.startup()
        job = self._repository.claim_next_queued_job()
        if job is None:
            return None
        if self._cancelled(job.job_id):
            return self._current_job(job.job_id)

        try:
            result_ref = self._executor.execute(job)
        except DomainError as error:
            return self._terminalize_failure(job, error.detail)
        except Exception:
            return self._terminalize_failure(
                job,
                ErrorDetail.for_code(ErrorCode.STORAGE, "job execution failed"),
            )

        if self._cancelled(job.job_id):
            return self._current_job(job.job_id)
        if not _is_immutable_result_reference(result_ref):
            return self._terminalize_failure(
                job,
                ErrorDetail.for_code(
                    ErrorCode.STORAGE,
                    "job executor returned no immutable result reference",
                ),
            )
        return self._transition_or_cancelled(
            job,
            target_status=JobStatus.SUCCEEDED,
            result_ref=result_ref,
        )

    def run_forever(self, stop_event: Event) -> None:
        """Poll until the caller signals shutdown; no job is resumed after restart."""

        self.startup()
        while not stop_event.is_set():
            completed = self.run_once()
            if completed is None:
                stop_event.wait(self._poll_interval_seconds)

    def run_until_idle(self) -> tuple[Job, ...]:
        """Drain currently queued work for explicit local-operation use only."""

        completed: list[Job] = []
        while True:
            job = self.run_once()
            if job is None:
                return tuple(completed)
            completed.append(job)

    def _terminalize_failure(self, job: Job, detail: ErrorDetail) -> Job:
        if self._cancelled(job.job_id):
            return self._current_job(job.job_id)
        return self._transition_or_cancelled(
            job,
            target_status=JobStatus.FAILED,
            error=detail,
        )

    def _transition_or_cancelled(
        self,
        job: Job,
        *,
        target_status: JobStatus,
        result_ref: str | None = None,
        error: ErrorDetail | None = None,
    ) -> Job:
        try:
            return self._repository.transition_job(
                job_id=job.job_id,
                expected_status=JobStatus.RUNNING,
                target_status=target_status,
                result_ref=result_ref,
                error=error,
            )
        except JobStateError:
            current = self._current_job(job.job_id)
            if current.status is JobStatus.CANCELLED:
                return current
            raise

    def _cancelled(self, job_id: str) -> bool:
        return self._current_job(job_id).status is JobStatus.CANCELLED

    def _current_job(self, job_id: str) -> Job:
        job = self._repository.get_job(job_id)
        if job is None:
            raise JobStateError(f"job disappeared while worker owned it: {job_id}")
        return job


def _is_immutable_result_reference(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and _RESULT_REFERENCE.fullmatch(value) is not None
    )


__all__ = ["PersistentWorker"]
