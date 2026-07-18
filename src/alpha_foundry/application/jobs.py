"""Durable job lifecycle repository and single-worker service."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from threading import Lock
from typing import ClassVar, Final
from uuid import uuid4

from alpha_foundry.application._timestamps import format_utc_timestamp, parse_utc_timestamp
from alpha_foundry.application.ports import Clock, JobExecutor, JobRepository
from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail, ErrorField
from alpha_foundry.domain.models import Job, JobKind, JobProgress, JobStatus
from alpha_foundry.infrastructure.artifacts import ArtifactStoreError
from alpha_foundry.infrastructure.db import SQLiteStore


class JobStateError(RuntimeError):
    """Raised when a requested job lifecycle transition is not legal."""


class SystemClock:
    """Production clock supplying timezone-aware UTC timestamps."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class SQLiteJobRepository:
    """SQLite implementation of explicit, compare-and-swap job transitions."""

    _TRANSITIONS: Final[dict[JobStatus, frozenset[JobStatus]]] = {
        JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
        JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}),
        JobStatus.SUCCEEDED: frozenset(),
        JobStatus.FAILED: frozenset(),
        JobStatus.CANCELLED: frozenset(),
    }
    _WORKER_OWNER: Final[str] = "single-worker"

    def __init__(self, store: SQLiteStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def create_job(
        self,
        *,
        job_id: str,
        kind: JobKind,
        resource_id: str,
        command_fingerprint: str,
        stage: str,
        progress: JobProgress | None = None,
    ) -> Job:
        """Durably enqueue a job, reusing a matching kind/fingerprint row."""
        if not command_fingerprint:
            raise ValueError("command_fingerprint must not be empty")
        if not stage:
            raise ValueError("stage must not be empty")

        now = self._timestamp(self._clock.now())
        with self._store.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM jobs WHERE kind = ? AND command_fingerprint = ?",
                (kind.value, command_fingerprint),
            ).fetchone()
            if existing is not None:
                if existing["resource_id"] != resource_id:
                    raise JobStateError(
                        "idempotent job fingerprint is already bound to a different resource"
                    )
                return self._job_from_row(existing)
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, kind, resource_id, status, stage, command_fingerprint,
                    progress_json, result_ref, error_json, execution_owner,
                    created_at, started_at, finished_at, updated_at, row_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, NULL, NULL, ?, 1)
                """,
                (
                    job_id,
                    kind.value,
                    resource_id,
                    JobStatus.QUEUED.value,
                    stage,
                    command_fingerprint,
                    self._progress_json(progress),
                    now,
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise RuntimeError("inserted job could not be read")
        return self._job_from_row(row)

    def get_job(self, job_id: str) -> Job | None:
        """Return a persisted job by ID."""
        row = self._store.fetch_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        return self._job_from_row(row) if row is not None else None

    def claim_next_queued_job(self) -> Job | None:
        """Atomically claim the oldest queued job for the one local worker."""
        now = self._timestamp(self._clock.now())
        with self._store.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = ?
                ORDER BY created_at ASC, job_id ASC
                LIMIT 1
                """,
                (JobStatus.QUEUED.value,),
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE jobs
                SET status = ?, started_at = ?, updated_at = ?, execution_owner = ?, row_version = row_version + 1
                WHERE job_id = ? AND status = ? AND row_version = ? AND execution_owner IS NULL
                """,
                (
                    JobStatus.RUNNING.value,
                    now,
                    now,
                    self._WORKER_OWNER,
                    row["job_id"],
                    JobStatus.QUEUED.value,
                    row["row_version"],
                ),
            )
            if updated.rowcount != 1:
                raise JobStateError("queued job could not be claimed")
            claimed = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)
            ).fetchone()
        if claimed is None:
            raise RuntimeError("claimed job could not be read")
        return self._job_from_row(claimed)

    def transition_job(
        self,
        *,
        job_id: str,
        expected_status: JobStatus,
        target_status: JobStatus,
        stage: str | None = None,
        progress: JobProgress | None = None,
        result_ref: str | None = None,
        error: ErrorDetail | None = None,
    ) -> Job:
        """Apply one legal state transition using an expected-status/version CAS."""
        if target_status not in self._TRANSITIONS[expected_status]:
            raise JobStateError(
                f"illegal job transition: {expected_status.value} -> {target_status.value}"
            )
        if stage is not None and not stage:
            raise ValueError("stage must not be empty")
        if target_status is JobStatus.FAILED and error is None:
            raise ValueError("failed jobs require an error")
        if target_status is not JobStatus.FAILED and error is not None:
            raise ValueError("only failed jobs may contain an error")
        if target_status is JobStatus.SUCCEEDED and (
            not isinstance(result_ref, str) or not result_ref
        ):
            raise ValueError("successful jobs require a result_ref")

        now = self._timestamp(self._clock.now())
        with self._store.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobStateError(f"job not found: {job_id}")
            actual_status = JobStatus(row["status"])
            if actual_status is not expected_status:
                raise JobStateError(
                    f"job status changed: expected {expected_status.value}, got {actual_status.value}"
                )

            started_at = row["started_at"]
            if target_status is JobStatus.CANCELLED and started_at is None:
                started_at = now
            terminal = target_status in {
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }
            updated = connection.execute(
                """
                UPDATE jobs
                SET status = ?, stage = ?, progress_json = ?, result_ref = ?, error_json = ?,
                    execution_owner = ?, started_at = ?, finished_at = ?, updated_at = ?,
                    row_version = row_version + 1
                WHERE job_id = ? AND status = ? AND row_version = ?
                """,
                (
                    target_status.value,
                    stage if stage is not None else row["stage"],
                    self._progress_json(progress) if progress is not None else row["progress_json"],
                    result_ref if target_status is JobStatus.SUCCEEDED else row["result_ref"],
                    self._error_json(error),
                    None if terminal else row["execution_owner"],
                    started_at,
                    now if terminal else row["finished_at"],
                    now,
                    job_id,
                    expected_status.value,
                    row["row_version"],
                ),
            )
            if updated.rowcount != 1:
                raise JobStateError("job transition lost compare-and-swap ownership")
            if target_status in {JobStatus.FAILED, JobStatus.CANCELLED}:
                self._revoke_owned_children(
                    connection,
                    owner_job_id=job_id,
                    now=now,
                )
            transitioned = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if transitioned is None:
            raise RuntimeError("transitioned job could not be read")
        return self._job_from_row(transitioned)

    def recover_interrupted_jobs(self) -> tuple[Job, ...]:
        """Unconditionally fail every persisted running job and release stale publication ownership."""
        now = self._timestamp(self._clock.now())
        interrupted = ErrorDetail.for_code(
            ErrorCode.JOB_INTERRUPTED,
            "job interrupted by process startup",
        )
        with self._store.transaction(immediate=True) as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY job_id ASC",
                (JobStatus.RUNNING.value,),
            ).fetchall()
            for row in rows:
                updated = connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, error_json = ?, execution_owner = NULL, finished_at = ?,
                        updated_at = ?, row_version = row_version + 1
                    WHERE job_id = ? AND status = ? AND row_version = ?
                    """,
                    (
                        JobStatus.FAILED.value,
                        self._error_json(interrupted),
                        now,
                        now,
                        row["job_id"],
                        JobStatus.RUNNING.value,
                        row["row_version"],
                    ),
                )
                if updated.rowcount != 1:
                    raise JobStateError("interrupted job recovery lost compare-and-swap ownership")

                self._revoke_owned_children(
                    connection,
                    owner_job_id=str(row["job_id"]),
                    now=now,
                )

            recovered_rows = [
                connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)
                ).fetchone()
                for row in rows
            ]
        return tuple(self._job_from_row(row) for row in recovered_rows if row is not None)

    @staticmethod
    def _revoke_owned_children(
        connection: sqlite3.Connection,
        *,
        owner_job_id: str,
        now: str,
    ) -> None:
        generation_rows = connection.execute(
            """
            SELECT * FROM generation_requests
            WHERE state = 'RUNNING' AND owner_job_id = ?
            ORDER BY generation_request_id ASC
            """,
            (owner_job_id,),
        ).fetchall()
        for request in generation_rows:
            attempts = connection.execute(
                """
                SELECT * FROM generation_attempts
                WHERE generation_request_id = ? AND state = 'STARTED' AND owner_epoch = ?
                ORDER BY ordinal ASC
                """,
                (request["generation_request_id"], request["lease_epoch"]),
            ).fetchall()
            next_ordinal = int(request["next_ordinal"])
            related_attempt_id: str | None = None
            for attempt in attempts:
                terminalized = connection.execute(
                    """
                    UPDATE generation_attempts
                    SET state = 'INTERRUPTED', error_code = ?, terminal_at = ?
                    WHERE attempt_id = ? AND state = 'STARTED' AND owner_epoch = ?
                    """,
                    (
                        ErrorCode.JOB_INTERRUPTED.value,
                        now,
                        attempt["attempt_id"],
                        request["lease_epoch"],
                    ),
                )
                if terminalized.rowcount != 1:
                    raise JobStateError(
                        "generation interruption recovery lost compare-and-swap ownership"
                    )
                next_ordinal = max(next_ordinal, int(attempt["ordinal"]) + 1)
                related_attempt_id = str(attempt["attempt_id"])

            try:
                provider_chain = json.loads(request["provider_chain_json"])
            except json.JSONDecodeError as error:
                raise JobStateError(
                    "generation provider chain is corrupt during child revocation"
                ) from error
            if not isinstance(provider_chain, list) or not provider_chain:
                raise JobStateError("generation provider chain is corrupt during child revocation")
            target_state = "FAILED" if next_ordinal >= len(provider_chain) else "AVAILABLE"
            released = connection.execute(
                """
                UPDATE generation_requests
                SET state = ?, owner_token = NULL, owner_job_id = NULL, lease_expires_at = NULL,
                    next_ordinal = ?, updated_at = ?, row_version = row_version + 1
                WHERE generation_request_id = ? AND state = 'RUNNING' AND owner_job_id = ?
                    AND owner_token = ? AND lease_epoch = ? AND row_version = ?
                """,
                (
                    target_state,
                    next_ordinal,
                    now,
                    request["generation_request_id"],
                    owner_job_id,
                    request["owner_token"],
                    request["lease_epoch"],
                    request["row_version"],
                ),
            )
            if released.rowcount != 1:
                raise JobStateError("generation child revocation lost compare-and-swap ownership")
            connection.execute(
                """
                INSERT INTO generation_owner_events (
                    event_id, generation_request_id, event_kind, old_owner_job_id,
                    old_owner_token, old_owner_epoch, new_owner_job_id, new_owner_token,
                    new_owner_epoch, related_attempt_id, prior_event_id, created_at
                ) VALUES (?, ?, 'RELEASED_INTERRUPTED', ?, ?, ?, NULL, NULL, NULL, ?, NULL, ?)
                """,
                (
                    str(uuid4()),
                    request["generation_request_id"],
                    owner_job_id,
                    request["owner_token"],
                    request["lease_epoch"],
                    related_attempt_id,
                    now,
                ),
            )

        publication_rows = connection.execute(
            """
            SELECT * FROM publications
            WHERE state = 'PREPARING' AND owner_job_id = ?
            ORDER BY publication_id ASC
            """,
            (owner_job_id,),
        ).fetchall()
        for publication in publication_rows:
            released = connection.execute(
                """
                UPDATE publications
                SET state = 'FAILED_RETRYABLE', owner_token = NULL, owner_job_id = NULL,
                    owner_expires_at = NULL, failure_kind = 'OWNER_INTERRUPTED',
                    updated_at = ?, row_version = row_version + 1
                WHERE publication_id = ? AND state = 'PREPARING' AND owner_job_id = ?
                    AND owner_token = ? AND owner_epoch = ? AND row_version = ?
                """,
                (
                    now,
                    publication["publication_id"],
                    owner_job_id,
                    publication["owner_token"],
                    publication["owner_epoch"],
                    publication["row_version"],
                ),
            )
            if released.rowcount != 1:
                raise JobStateError("publication child revocation lost compare-and-swap ownership")
            for event_kind in ("RELEASED_STALE", "FAILED_ATTEMPT"):
                connection.execute(
                    """
                    INSERT INTO publication_owner_events (
                        event_id, publication_id, event_kind, owner_job_id, owner_epoch,
                        orphan_hashes_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        publication["publication_id"],
                        event_kind,
                        owner_job_id,
                        publication["owner_epoch"],
                        publication["prepared_orphan_hashes_json"],
                        now,
                    ),
                )

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return format_utc_timestamp(value)

    @staticmethod
    def _progress_json(progress: JobProgress | None) -> str:
        if progress is None:
            return "null"
        return json.dumps(
            progress.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _error_json(error: ErrorDetail | None) -> str | None:
        if error is None:
            return None
        return json.dumps(
            error.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> Job:
        progress_json = row["progress_json"]
        error_json = row["error_json"]
        progress = (
            JobProgress.model_validate(json.loads(progress_json))
            if progress_json != "null"
            else None
        )
        error = ErrorDetail.model_validate_json(error_json) if error_json is not None else None
        return Job(
            job_id=row["job_id"],
            kind=JobKind(row["kind"]),
            resource_id=row["resource_id"],
            status=JobStatus(row["status"]),
            stage=row["stage"],
            progress=progress,
            error=error,
            result_ref=row["result_ref"],
            created_at=SQLiteJobRepository._parse_timestamp(row["created_at"]),
            started_at=SQLiteJobRepository._optional_timestamp(row["started_at"]),
            finished_at=SQLiteJobRepository._optional_timestamp(row["finished_at"]),
        )

    @staticmethod
    def _optional_timestamp(value: str | None) -> datetime | None:
        return SQLiteJobRepository._parse_timestamp(value) if value is not None else None

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        return parse_utc_timestamp(value)


class PersistentSingleWorkerJobService:
    """Runs at most one durable job and never resumes jobs from a prior process."""

    _PROCESS_LOCK: ClassVar[Lock] = Lock()
    _STARTED_REPOSITORIES: ClassVar[dict[int, JobRepository]] = {}

    def __init__(self, repository: JobRepository) -> None:
        self._repository = repository
        self._worker_lock = self._PROCESS_LOCK

    def startup(self) -> tuple[Job, ...]:
        """Run mandatory restart recovery before this process can claim any work."""
        with self._worker_lock:
            return self._startup()

    def _startup(self) -> tuple[Job, ...]:
        repository_key = id(self._repository)
        if self._STARTED_REPOSITORIES.get(repository_key) is self._repository:
            return ()
        recovered = self._repository.recover_interrupted_jobs()
        self._STARTED_REPOSITORIES[repository_key] = self._repository
        return recovered

    def run_once(self, executor: JobExecutor) -> Job | None:
        """Claim, execute, and complete at most one job under the local worker lock."""
        with self._worker_lock:
            self._startup()
            return self._run_once(executor)

    def _run_once(self, executor: JobExecutor) -> Job | None:
        job = self._repository.claim_next_queued_job()
        if job is None:
            return None
        try:
            result_ref = executor.execute(job)
        except DomainError as error:
            return self._repository.transition_job(
                job_id=job.job_id,
                expected_status=JobStatus.RUNNING,
                target_status=JobStatus.FAILED,
                error=error.detail,
            )
        except ArtifactStoreError as error:
            self._repository.transition_job(
                job_id=job.job_id,
                expected_status=JobStatus.RUNNING,
                target_status=JobStatus.FAILED,
                error=ErrorDetail.for_code(
                    ErrorCode.STATE,
                    "artifact store integrity failure",
                    details=(
                        ErrorField(
                            path="exception_type",
                            reason=type(error).__name__,
                        ),
                    ),
                ),
            )
            raise
        except (OSError, sqlite3.OperationalError):
            self._repository.transition_job(
                job_id=job.job_id,
                expected_status=JobStatus.RUNNING,
                target_status=JobStatus.FAILED,
                error=ErrorDetail.for_code(
                    ErrorCode.STORAGE, "transient job execution storage failure"
                ),
            )
            raise
        except Exception as error:
            self._repository.transition_job(
                job_id=job.job_id,
                expected_status=JobStatus.RUNNING,
                target_status=JobStatus.FAILED,
                error=ErrorDetail.for_code(
                    ErrorCode.STATE,
                    "unexpected job execution failure",
                    details=(
                        ErrorField(
                            path="exception_type",
                            reason=type(error).__name__,
                        ),
                    ),
                ),
            )
            raise
        if not isinstance(result_ref, str) or not result_ref:
            return self._repository.transition_job(
                job_id=job.job_id,
                expected_status=JobStatus.RUNNING,
                target_status=JobStatus.FAILED,
                error=ErrorDetail.for_code(
                    ErrorCode.STATE,
                    "job executor returned no immutable result reference",
                ),
            )
        completed = self._repository.get_job(job.job_id)
        if completed is not None and completed.status is JobStatus.SUCCEEDED:
            if completed.result_ref != result_ref:
                raise JobStateError(
                    "atomic job completion result does not match the executor result reference"
                )
            return completed
        return self._repository.transition_job(
            job_id=job.job_id,
            expected_status=JobStatus.RUNNING,
            target_status=JobStatus.SUCCEEDED,
            result_ref=result_ref,
        )

    def cancel(self, job_id: str) -> Job:
        """Cancel a queued or running job through its explicit legal transition."""
        job = self._repository.get_job(job_id)
        if job is None:
            raise JobStateError(f"job not found: {job_id}")
        return self._repository.transition_job(
            job_id=job_id,
            expected_status=job.status,
            target_status=JobStatus.CANCELLED,
        )
