from __future__ import annotations

import json
from pathlib import Path

from alpha_foundry.application.jobs import SQLiteJobRepository, SystemClock
from alpha_foundry.bootstrap import BootstrapSettings, bootstrap
from alpha_foundry.domain.canonical import digest
from alpha_foundry.domain.errors import ErrorCode, ErrorDetail
from alpha_foundry.domain.models import JobStatus
from alpha_foundry.infrastructure.db import SQLiteStore


def _mandate() -> dict[str, object]:
    return {
        "mandate_id": "mandate-1",
        "version": "1.0.0",
        "mode": "DOMAIN",
        "domain": "FACTOR",
        "objective": "Evaluate a deterministic factor mandate.",
        "data_requirements": ["synthetic-factor-data"],
        "budget": {"max_candidates": 1},
        "forbidden_conditions": ["cross-domain composition"],
    }


def test_resubmit_creates_one_queued_job_linked_to_the_failed_user_command(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "metadata.sqlite"
    runtime = bootstrap(
        BootstrapSettings(
            database_path=database_path,
            artifact_root=tmp_path / "cas",
            llm_provider="fake",
        )
    )
    inspection: SQLiteStore | None = None
    try:
        runtime.create_mandate(_mandate(), idempotency_key="mandate-create")
        submitted = runtime.run_mandate("mandate-1", idempotency_key="mandate-run")
        source_job_id = submitted["job_id"]
        assert isinstance(source_job_id, str)
        inspection = SQLiteStore(database_path)
        job_repository = SQLiteJobRepository(inspection, SystemClock())
        claimed = job_repository.claim_next_queued_job()
        assert claimed is not None
        assert claimed.job_id == source_job_id
        failed = job_repository.transition_job(
            job_id=source_job_id,
            expected_status=JobStatus.RUNNING,
            target_status=JobStatus.FAILED,
            error=ErrorDetail.for_code(ErrorCode.STORAGE, "simulated execution failure"),
        )
        assert failed.status is JobStatus.FAILED

        resubmitted = runtime.resubmit_job(source_job_id, idempotency_key="resubmit-once")
        repeated = runtime.resubmit_job(source_job_id, idempotency_key="resubmit-once")

        new_job_id = resubmitted["job_id"]
        assert isinstance(new_job_id, str)
        assert new_job_id != source_job_id
        assert repeated == resubmitted
        assert resubmitted["job"]["status"] == JobStatus.QUEUED.value
        assert resubmitted["job"]["stage"] == "RESUBMIT"
        assert resubmitted["job"]["kind"] == failed.kind.value

        expected_fingerprint = digest(
            "AF:COMMAND:1",
            {
                "operation": "job.resubmit",
                "payload": {
                    "kind": failed.kind.value,
                    "resource_id": failed.resource_id,
                    "source_job_id": source_job_id,
                },
            },
        )
        assert runtime.get_job(source_job_id)["job"]["status"] == JobStatus.FAILED.value
        assert runtime.get_job(new_job_id)["job"]["status"] == JobStatus.QUEUED.value

        persisted_link = inspection.fetch_one(
            """
            SELECT request_hash, job_id, final_response_json
            FROM idempotency_keys
            WHERE operation = ? AND idempotency_key = ?
            """,
            ("job.resubmit", "resubmit-once"),
        )
        assert persisted_link is not None
        assert persisted_link["request_hash"] == expected_fingerprint
        assert persisted_link["job_id"] == new_job_id
        assert json.loads(persisted_link["final_response_json"]) == resubmitted
    finally:
        if inspection is not None:
            inspection.close()
        runtime.close()
