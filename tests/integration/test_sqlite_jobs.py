from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alpha_foundry.application.jobs import JobStateError, SQLiteJobRepository
from alpha_foundry.domain.errors import ErrorCode
from alpha_foundry.domain.models import JobKind, JobStatus
from alpha_foundry.infrastructure.db import SQLiteStore


@dataclass(frozen=True)
class FixedClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


def _repository(tmp_path: Path) -> tuple[SQLiteStore, SQLiteJobRepository]:
    path = tmp_path / "metadata.sqlite"
    store = SQLiteStore(path)
    clock = FixedClock(datetime(2026, 7, 12, 9, 30, tzinfo=UTC))
    return store, SQLiteJobRepository(store, clock)


def test_sqlite_schema_contains_durable_job_and_generation_ledgers(tmp_path: Path) -> None:
    store, _ = _repository(tmp_path)
    try:
        tables = {
            row["name"]
            for row in store.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {
            "jobs",
            "generation_requests",
            "generation_owner_events",
            "generation_attempts",
            "knowledge_claims",
            "knowledge_packs",
            "knowledge_pack_claims",
            "artifacts",
        } <= tables

        job_columns = {row["name"] for row in store.fetch_all("PRAGMA table_info(jobs)")}
        assert {
            "job_id",
            "status",
            "command_fingerprint",
            "execution_owner",
            "started_at",
            "finished_at",
            "row_version",
        } <= job_columns

        attempt_columns = {
            row["name"] for row in store.fetch_all("PRAGMA table_info(generation_attempts)")
        }
        assert {
            "generation_request_id",
            "ordinal",
            "provider",
            "model",
            "state",
            "error_code",
            "owner_epoch",
        } <= attempt_columns
    finally:
        store.close()


def test_restart_recovery_fails_every_running_job_without_resuming_queued_work(
    tmp_path: Path,
) -> None:
    store, repository = _repository(tmp_path)
    try:
        for job_id in ("job-b", "job-a", "job-queued"):
            repository.create_job(
                job_id=job_id,
                kind=JobKind.GENERATE,
                resource_id=f"resource-{job_id}",
                command_fingerprint=f"fingerprint-{job_id}",
                stage="GENERATE",
            )

        first = repository.claim_next_queued_job()
        second = repository.claim_next_queued_job()
        assert first is not None
        assert second is not None
        assert {first.job_id, second.job_id} == {"job-a", "job-b"}
        assert first.status is JobStatus.RUNNING
        assert second.status is JobStatus.RUNNING

        recovered = repository.recover_interrupted_jobs()

        assert tuple(job.job_id for job in recovered) == ("job-a", "job-b")
        assert all(job.status is JobStatus.FAILED for job in recovered)
        assert all(job.error is not None for job in recovered)
        assert {job.error.code for job in recovered if job.error is not None} == {
            ErrorCode.JOB_INTERRUPTED
        }
        assert all(job.finished_at is not None for job in recovered)
        queued = repository.get_job("job-queued")
        assert queued is not None
        assert queued.status is JobStatus.QUEUED

        rows = store.fetch_all(
            "SELECT job_id, execution_owner, row_version FROM jobs WHERE job_id != ? ORDER BY job_id",
            ("job-queued",),
        )
        assert [(row["job_id"], row["execution_owner"], row["row_version"]) for row in rows] == [
            ("job-a", None, 3),
            ("job-b", None, 3),
        ]
    finally:
        store.close()


def test_job_fingerprint_is_idempotent_and_terminal_jobs_cannot_transition(tmp_path: Path) -> None:
    store, repository = _repository(tmp_path)
    try:
        created = repository.create_job(
            job_id="job-original",
            kind=JobKind.RUN_SEARCH,
            resource_id="search-1",
            command_fingerprint="same-command",
            stage="SEARCH",
        )
        duplicate = repository.create_job(
            job_id="job-ignored",
            kind=JobKind.RUN_SEARCH,
            resource_id="search-1",
            command_fingerprint="same-command",
            stage="SEARCH",
        )
        assert duplicate.job_id == created.job_id

        with pytest.raises(JobStateError, match="already bound to a different resource"):
            repository.create_job(
                job_id="job-conflict",
                kind=JobKind.RUN_SEARCH,
                resource_id="search-2",
                command_fingerprint="same-command",
                stage="SEARCH",
            )

        running = repository.claim_next_queued_job()
        assert running is not None
        completed = repository.transition_job(
            job_id=running.job_id,
            expected_status=JobStatus.RUNNING,
            target_status=JobStatus.SUCCEEDED,
            result_ref="cas://sha256/result",
        )
        assert completed.status is JobStatus.SUCCEEDED

        with pytest.raises(JobStateError, match="illegal job transition"):
            repository.transition_job(
                job_id=completed.job_id,
                expected_status=JobStatus.SUCCEEDED,
                target_status=JobStatus.RUNNING,
            )
    finally:
        store.close()
