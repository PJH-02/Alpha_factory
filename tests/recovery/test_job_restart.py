from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alpha_foundry.application.jobs import PersistentSingleWorkerJobService, SQLiteJobRepository
from alpha_foundry.domain.errors import ErrorCode
from alpha_foundry.domain.models import JobKind, JobStatus
from alpha_foundry.infrastructure.db import SQLiteStore


@dataclass(frozen=True)
class FixedClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


def _repository(tmp_path: Path) -> tuple[SQLiteStore, SQLiteJobRepository]:
    store = SQLiteStore(tmp_path / "metadata.sqlite")
    repository = SQLiteJobRepository(
        store,
        FixedClock(datetime(2026, 7, 12, 9, 30, tzinfo=UTC)),
    )
    return store, repository


def _seed_consumed_holdout(store: SQLiteStore) -> None:
    timestamp = "2026-07-12T09:30:00.000000Z"
    with store.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO lineages (
                lineage_id, status, closed_at, close_reason, root_refs_json, created_at
            ) VALUES (?, 'CLOSED', ?, 'HOLDOUT_CONSUMED', '{}', ?)
            """,
            ("closed-lineage", timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO validations (
                validation_id, strategy_id, candidate_hash, lineage_id, profile_hash,
                pre_validation_decision, gate_summary_json, fold_summary_json,
                pbo_certificate_id, content_hash, created_at, created_by,
                schema_version, code_version
            ) VALUES (?, ?, ?, ?, ?, 'PASS', '{}', '{}', NULL, ?, ?, ?, ?, ?)
            """,
            (
                "closed-validation",
                "strategy-1",
                "candidate-1",
                "closed-lineage",
                "sha256:" + "a" * 64,
                "sha256:" + "b" * 64,
                timestamp,
                "test",
                "test-v1",
                "test-v1",
            ),
        )
        connection.execute(
            """
            INSERT INTO holdout_slots (
                lineage_id, profile_hash, state, consumed_at, consuming_validation_id,
                selector_hash, row_version
            ) VALUES (?, ?, 'CONSUMED', ?, ?, ?, 1)
            """,
            (
                "closed-lineage",
                "sha256:" + "a" * 64,
                timestamp,
                "closed-validation",
                "sha256:" + "c" * 64,
            ),
        )


def test_restart_fails_every_running_job_without_changing_completed_or_closed_authority(
    tmp_path: Path,
) -> None:
    store, repository = _repository(tmp_path)
    try:
        _seed_consumed_holdout(store)
        for job_id, kind, fingerprint in (
            ("running-generate", JobKind.GENERATE, "generate-running"),
            ("running-publish", JobKind.PUBLISH, "publish-running"),
        ):
            repository.create_job(
                job_id=job_id,
                kind=kind,
                resource_id=f"resource-{job_id}",
                command_fingerprint=fingerprint,
                stage="WORK",
            )

        completed = repository.create_job(
            job_id="completed-job",
            kind=JobKind.RUN_EXPERIMENT,
            resource_id="completed-resource",
            command_fingerprint="completed-fingerprint",
            stage="EXPERIMENT",
        )
        for _ in range(3):
            claimed = repository.claim_next_queued_job()
            assert claimed is not None
            if claimed.job_id == completed.job_id:
                repository.transition_job(
                    job_id=claimed.job_id,
                    expected_status=JobStatus.RUNNING,
                    target_status=JobStatus.SUCCEEDED,
                    result_ref="cas://sha256/completed-result",
                )

        service = PersistentSingleWorkerJobService(repository)
        recovered = service.startup()

        assert tuple(job.job_id for job in recovered) == ("running-generate", "running-publish")
        assert all(job.status is JobStatus.FAILED for job in recovered)
        assert all(job.error is not None for job in recovered)
        assert {job.error.code for job in recovered if job.error is not None} == {
            ErrorCode.JOB_INTERRUPTED
        }
        assert all(job.finished_at is not None for job in recovered)
        assert service.startup() == ()

        unchanged_completed = repository.get_job(completed.job_id)
        assert unchanged_completed is not None
        assert unchanged_completed.status is JobStatus.SUCCEEDED
        assert unchanged_completed.result_ref == "cas://sha256/completed-result"
        duplicate = repository.create_job(
            job_id="replacement-id-must-not-be-used",
            kind=JobKind.RUN_EXPERIMENT,
            resource_id="completed-resource",
            command_fingerprint="completed-fingerprint",
            stage="EXPERIMENT",
        )
        assert duplicate.job_id == completed.job_id
        assert duplicate.status is JobStatus.SUCCEEDED
        assert repository.claim_next_queued_job() is None

        holdout = store.fetch_one(
            """
            SELECT state, consuming_validation_id, selector_hash
            FROM holdout_slots WHERE lineage_id = ?
            """,
            ("closed-lineage",),
        )
        lineage = store.fetch_one(
            "SELECT status, close_reason FROM lineages WHERE lineage_id = ?",
            ("closed-lineage",),
        )
        assert holdout is not None
        assert tuple(holdout) == ("CONSUMED", "closed-validation", "sha256:" + "c" * 64)
        assert lineage is not None
        assert tuple(lineage) == ("CLOSED", "HOLDOUT_CONSUMED")

        with pytest.raises(sqlite3.IntegrityError, match="consumed holdout slot is immutable"):
            with store.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE holdout_slots SET state = 'UNUSED' WHERE lineage_id = ?",
                    ("closed-lineage",),
                )
        with pytest.raises(sqlite3.IntegrityError, match="closed lineage is immutable"):
            with store.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE lineages SET status = 'OPEN' WHERE lineage_id = ?",
                    ("closed-lineage",),
                )
    finally:
        store.close()
