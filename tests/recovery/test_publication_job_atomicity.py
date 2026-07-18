from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from alpha_foundry.application.jobs import PersistentSingleWorkerJobService, SQLiteJobRepository
from alpha_foundry.application.publication import (
    PublicationCommand,
    PublicationService,
    SQLitePublicationRepository,
)
from alpha_foundry.domain.errors import ErrorCode
from alpha_foundry.domain.models import (
    Decision,
    DisclosedMetric,
    Domain,
    GateComparison,
    JobKind,
    JobStatus,
    RankingDirection,
    RankingRule,
    ResearchReport,
    ValidationGate,
    ValidationProfile,
)
from alpha_foundry.infrastructure.artifacts import LocalArtifactStore
from alpha_foundry.infrastructure.db import SQLiteStore
from alpha_foundry.publication import PublicationState
from alpha_foundry.validation.disclosure import HoldoutDisclosure, HoldoutDisclosurePolicy

_NOW = datetime(2026, 7, 12, 9, 30, tzinfo=UTC)
_TIMESTAMP = "2026-07-12T09:30:00.000000Z"


@dataclass(frozen=True)
class FixedClock:
    instant: datetime = _NOW

    def now(self) -> datetime:
        return self.instant


def _hash(character: str) -> str:
    return f"sha256:{ord(character):064x}"


def _command(
    *,
    publication_id: str = "publication-1",
    validation_id: str = "validation-1",
    lineage_id: str = "lineage-1",
    owner_job_id: str = "publish-job",
    owner_token: str = "owner-token",
    registry_entry_id: str = "registry-1",
) -> PublicationCommand:
    profile = ValidationProfile(
        validation_profile_id="profile-1",
        domain=Domain.FACTOR,
        version="1.0.0",
        profile_hash=_hash("a"),
        hard_gates=(
            ValidationGate(
                gate_id="sharpe-gate",
                metric_name="sharpe",
                comparison=GateComparison.GTE,
                threshold=Decimal("1"),
            ),
        ),
        ranking=(
            RankingRule(
                metric_name="sharpe",
                direction=RankingDirection.DESC,
                quantization=Decimal("0.01"),
            ),
        ),
        fold_ids=("fold-1",),
        pbo_minimum_eligible=1,
        patience=1,
        holdout_policy_hash=_hash("b"),
        disclosure_policy_hash=_hash("c"),
        allowed_disclosure_fields=("sharpe",),
    )
    disclosure = HoldoutDisclosure(
        decision=Decision.PASS,
        disclosed_metrics=(
            DisclosedMetric(
                name="sharpe",
                value=Decimal("1.25"),
                threshold=Decimal("1"),
                decision=Decision.PASS,
            ),
        ),
    )
    report = ResearchReport(
        publication_id=publication_id,
        strategy_id="strategy-1",
        domain=Domain.FACTOR,
        evidence_ids=("evidence-1",),
        provider_attempt_ids=("attempt-1",),
        dataset_hashes=(_hash("d"),),
        config_hash=_hash("e"),
        code_hash=_hash("f"),
        schema_hashes=(_hash("g"),),
        lineage_id=lineage_id,
        validation_decision=Decision.PASS,
        disclosed_metrics=disclosure.disclosed_metrics,
        artifact_hashes=(_hash("h"),),
        created_at=_NOW,
    )
    command = PublicationCommand(
        publication_id=publication_id,
        validation_id=validation_id,
        strategy_id="strategy-1",
        candidate_hash="candidate-1",
        lineage_id=lineage_id,
        registry_entry_id=registry_entry_id,
        owner_job_id=owner_job_id,
        owner_token=owner_token,
        owner_expires_at=_NOW + timedelta(minutes=5),
        validation_profile=profile,
        validation_decision=Decision.PASS,
        validation_complete=True,
        lineage_closed=True,
        holdout_complete=True,
        disclosure=disclosure,
        disclosure_policy=HoldoutDisclosurePolicy(
            policy_id="disclosure-policy-1",
            version="1.0.0",
            policy_hash=_hash("c"),
            allowed_disclosure_fields=("sharpe",),
        ),
        report=report,
        input_hash=_hash("i"),
    )
    return replace(command, input_hash=command.expected_input_hash)


def _seed_eligible_inputs(
    store: SQLiteStore,
    *,
    lineage_id: str = "lineage-1",
    validation_id: str = "validation-1",
) -> None:
    with store.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO lineages (
                lineage_id, status, closed_at, close_reason, root_refs_json, created_at
            ) VALUES (?, 'CLOSED', ?, 'HOLDOUT_CONSUMED', '{}', ?)
            """,
            (lineage_id, _TIMESTAMP, _TIMESTAMP),
        )
        connection.execute(
            """
            INSERT INTO search_runs (
                search_run_id, lineage_id, search_spec_hash, universe_hash, profile_hash,
                state, stop_reason, best_candidate_hash, plateau_counter,
                created_at, updated_at, row_version
            ) VALUES (?, ?, ?, ?, ?, 'SUCCEEDED', 'PLATEAU', ?, 0, ?, ?, 1)
            """,
            (
                f"search-run-{lineage_id}",
                lineage_id,
                _hash("j"),
                _hash("k"),
                _hash("a"),
                "candidate-1",
                _TIMESTAMP,
                _TIMESTAMP,
            ),
        )
        connection.execute(
            """
            INSERT INTO pbo_certificates (
                pbo_certificate_id, search_run_id, profile_hash, started_trial_ids_json,
                terminal_trial_ids_json, eligible_trial_ids_json, matrix_trial_ids_json,
                expected_fold_ids_json, observed_fold_ids_json, started_count, terminal_count,
                eligible_count, matrix_count, artifact_id, decision, reason_code, created_at,
                created_by, schema_version, code_version
            ) VALUES (?, ?, ?, '["trial-1"]', '["trial-1"]', '["trial-1"]', '["trial-1"]',
                '["fold-1"]', '["fold-1"]', 1, 1, 1, 1, NULL, 'PASS', NULL, ?, ?, ?, ?)
            """,
            (
                f"pbo-{lineage_id}",
                f"search-run-{lineage_id}",
                _hash("a"),
                _TIMESTAMP,
                "test",
                "test-v1",
                "test-v1",
            ),
        )
        connection.execute(
            """
            INSERT INTO validations (
                validation_id, strategy_id, candidate_hash, lineage_id, profile_hash,
                pre_validation_decision, gate_summary_json, fold_summary_json,
                pbo_certificate_id, content_hash, created_at, created_by,
                schema_version, code_version
            ) VALUES (?, ?, ?, ?, ?, 'PASS', '{}', '{}', ?, ?, ?, ?, ?, ?)
            """,
            (
                validation_id,
                "strategy-1",
                "candidate-1",
                lineage_id,
                _hash("a"),
                f"pbo-{lineage_id}",
                _hash("l" if lineage_id == "lineage-1" else "m"),
                _TIMESTAMP,
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
                lineage_id,
                _hash("a"),
                _TIMESTAMP,
                validation_id,
                _hash("n"),
            ),
        )


def _running_publish_job(store: SQLiteStore, *, job_id: str = "publish-job") -> SQLiteJobRepository:
    jobs = SQLiteJobRepository(store, FixedClock())
    jobs.create_job(
        job_id=job_id,
        kind=JobKind.PUBLISH,
        resource_id="publication-1",
        command_fingerprint=f"publish-{job_id}",
        stage="PUBLISH",
    )
    claimed = jobs.claim_next_queued_job()
    assert claimed is not None
    assert claimed.job_id == job_id
    assert claimed.status is JobStatus.RUNNING
    return jobs


def test_publication_links_registry_state_and_owner_job_succeed_in_one_outcome(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "metadata.sqlite")
    try:
        _seed_eligible_inputs(store)
        jobs = _running_publish_job(store)
        artifacts = LocalArtifactStore(tmp_path / "cas")
        repository = SQLitePublicationRepository(store, FixedClock())

        outcome = PublicationService(repository, artifacts).publish(_command())

        assert outcome.publication is not None
        assert outcome.publication.state is PublicationState.PUBLISHED
        job = jobs.get_job("publish-job")
        assert job is not None
        assert job.status is JobStatus.SUCCEEDED
        assert job.stage == "PUBLISHED"
        assert job.result_ref is not None
        assert artifacts.read_bytes(job.result_ref)
        assert repository.get_published("publication-1") is not None
        assert len(repository.list_published()) == 1
        assert store.fetch_one("SELECT COUNT(*) AS count FROM artifacts")["count"] == 2
        assert store.fetch_one("SELECT COUNT(*) AS count FROM artifact_links")["count"] == 2
        assert store.fetch_one("SELECT COUNT(*) AS count FROM registry_entries")["count"] == 1
        assert (
            store.fetch_one(
                "SELECT state FROM publications WHERE publication_id = ?", ("publication-1",)
            )["state"]
            == "PUBLISHED"
        )
        assert {
            row["event_kind"]
            for row in store.fetch_all("SELECT event_kind FROM publication_owner_events")
        } == {"ACQUIRED", "PUBLISHED"}
    finally:
        store.close()


def test_restart_fails_legacy_running_owner_without_demoting_published_authority(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "metadata.sqlite")
    try:
        _seed_eligible_inputs(store)
        jobs = _running_publish_job(store)
        with store.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO publications (
                    publication_id, validation_id, strategy_id, candidate_hash, lineage_id,
                    profile_hash, registry_entry_id, state, owner_token, owner_job_id, owner_epoch,
                    owner_expires_at, attempt_count, prepared_orphan_hashes_json, failure_kind,
                    created_at, updated_at, published_at, row_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'AVAILABLE', NULL, NULL, 1, NULL, 1,
                    NULL, NULL, ?, ?, NULL, 1)
                """,
                (
                    "publication-1",
                    "validation-1",
                    "strategy-1",
                    "candidate-1",
                    "lineage-1",
                    _hash("a"),
                    "registry-1",
                    _TIMESTAMP,
                    _TIMESTAMP,
                ),
            )
            connection.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, cas_uri, content_hash, byte_size, media_type, created_at,
                    created_by, schema_version, code_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-report-json",
                    "cas://sha256/legacy-report-json",
                    _hash("o"),
                    1,
                    "application/json",
                    _TIMESTAMP,
                    "test",
                    "research-report-v1",
                    "publication-service-v1",
                ),
            )
            connection.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, cas_uri, content_hash, byte_size, media_type, created_at,
                    created_by, schema_version, code_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-report-html",
                    "cas://sha256/legacy-report-html",
                    _hash("p"),
                    1,
                    "text/html",
                    _TIMESTAMP,
                    "test",
                    "research-report-v1",
                    "publication-service-v1",
                ),
            )
            connection.execute(
                """
                INSERT INTO artifact_links (
                    artifact_link_id, artifact_id, owner_type, owner_id, role, created_at
                ) VALUES (?, ?, 'PUBLICATION', ?, ?, ?)
                """,
                (
                    "legacy-report-json-link",
                    "legacy-report-json",
                    "publication-1",
                    "RESEARCH_REPORT_JSON",
                    _TIMESTAMP,
                ),
            )
            connection.execute(
                """
                INSERT INTO artifact_links (
                    artifact_link_id, artifact_id, owner_type, owner_id, role, created_at
                ) VALUES (?, ?, 'PUBLICATION', ?, ?, ?)
                """,
                (
                    "legacy-report-html-link",
                    "legacy-report-html",
                    "publication-1",
                    "RESEARCH_REPORT_HTML",
                    _TIMESTAMP,
                ),
            )
            connection.execute(
                """
                INSERT INTO registry_entries (
                    registry_entry_id, publication_id, validation_id, strategy_id,
                    candidate_hash, lineage_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "registry-1",
                    "publication-1",
                    "validation-1",
                    "strategy-1",
                    "candidate-1",
                    "lineage-1",
                    _TIMESTAMP,
                ),
            )
            connection.execute(
                """
                UPDATE publications
                SET state = 'PUBLISHED', updated_at = ?, published_at = ?, row_version = row_version + 1
                WHERE publication_id = ? AND state = 'AVAILABLE'
                """,
                (_TIMESTAMP, _TIMESTAMP, "publication-1"),
            )

        recovered = PersistentSingleWorkerJobService(jobs).startup()

        assert tuple(job.job_id for job in recovered) == ("publish-job",)
        assert recovered[0].status is JobStatus.FAILED
        assert recovered[0].error is not None
        assert recovered[0].error.code is ErrorCode.JOB_INTERRUPTED
        authoritative = SQLitePublicationRepository(store, FixedClock()).get_publication(
            "publication-1"
        )
        assert authoritative is not None
        assert authoritative.state is PublicationState.PUBLISHED
        assert authoritative.published_at == _NOW
        published = SQLitePublicationRepository(store, FixedClock()).list_published()
        assert len(published) == 1
        assert published[0].registry_entry.publication_id == "publication-1"
    finally:
        store.close()
