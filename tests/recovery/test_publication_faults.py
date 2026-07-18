from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
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
from alpha_foundry.infrastructure.artifacts import LocalArtifactStore, StoredArtifact
from alpha_foundry.infrastructure.db import SQLiteStore
from alpha_foundry.publication import PublicationFailureKind, PublicationState
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


def _profile() -> ValidationProfile:
    return ValidationProfile(
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


def _policy() -> HoldoutDisclosurePolicy:
    return HoldoutDisclosurePolicy(
        policy_id="disclosure-policy-1",
        version="1.0.0",
        policy_hash=_hash("c"),
        allowed_disclosure_fields=("sharpe",),
    )


def _command(
    *,
    owner_job_id: str = "publish-job",
    owner_token: str = "owner-token",
    registry_entry_id: str = "registry-1",
) -> PublicationCommand:
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
        publication_id="publication-1",
        strategy_id="strategy-1",
        domain=Domain.FACTOR,
        evidence_ids=("evidence-1",),
        provider_attempt_ids=("attempt-1",),
        dataset_hashes=(_hash("d"),),
        config_hash=_hash("e"),
        code_hash=_hash("f"),
        schema_hashes=(_hash("g"),),
        lineage_id="lineage-1",
        validation_decision=Decision.PASS,
        disclosed_metrics=disclosure.disclosed_metrics,
        artifact_hashes=(_hash("h"),),
        created_at=_NOW,
    )
    command = PublicationCommand(
        publication_id="publication-1",
        validation_id="validation-1",
        strategy_id="strategy-1",
        candidate_hash="candidate-1",
        lineage_id="lineage-1",
        registry_entry_id=registry_entry_id,
        owner_job_id=owner_job_id,
        owner_token=owner_token,
        owner_expires_at=_NOW + timedelta(minutes=5),
        validation_profile=_profile(),
        validation_decision=Decision.PASS,
        validation_complete=True,
        lineage_closed=True,
        holdout_complete=True,
        disclosure=disclosure,
        disclosure_policy=_policy(),
        report=report,
        input_hash=_hash("i"),
    )
    return replace(command, input_hash=command.expected_input_hash)


def _seed_eligible_publication_inputs(store: SQLiteStore) -> None:
    with store.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO lineages (
                lineage_id, status, closed_at, close_reason, root_refs_json, created_at
            ) VALUES (?, 'CLOSED', ?, 'HOLDOUT_CONSUMED', '{}', ?)
            """,
            ("lineage-1", _TIMESTAMP, _TIMESTAMP),
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
                "search-run-1",
                "lineage-1",
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
                "pbo-1",
                "search-run-1",
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
                "validation-1",
                "strategy-1",
                "candidate-1",
                "lineage-1",
                _hash("a"),
                "pbo-1",
                _hash("l"),
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
                "lineage-1",
                _hash("a"),
                _TIMESTAMP,
                "validation-1",
                _hash("m"),
            ),
        )


def _running_publish_job(store: SQLiteStore, job_id: str = "publish-job") -> SQLiteJobRepository:
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


@dataclass
class FailSecondStageStore:
    delegate: LocalArtifactStore
    staged: list[StoredArtifact] = field(default_factory=list)

    def put_bytes(self, payload: bytes, media_type: str) -> StoredArtifact:
        if self.staged:
            raise OSError("simulated HTML artifact staging cut")
        artifact = self.delegate.put_bytes(payload, media_type)
        self.staged.append(artifact)
        return artifact

    def read_bytes(self, reference: str) -> bytes:
        return self.delegate.read_bytes(reference)


class FailingOwnerCompletionConnection:
    def __init__(self, connection: object, store: FailingOwnerCompletionStore) -> None:
        self._connection = connection
        self._store = store

    def execute(self, statement: str, parameters: object = ()) -> object:
        if "SET status = 'SUCCEEDED', stage = 'PUBLISHED'" in statement:
            self._store.failed_owner_completion_updates += 1
            raise sqlite3.OperationalError("simulated database authority cut")
        return self._connection.execute(statement, parameters)  # type: ignore[union-attr]

    def __getattr__(self, name: str) -> object:
        return getattr(self._connection, name)


class FailingOwnerCompletionStore(SQLiteStore):
    def __init__(self, database_path: Path) -> None:
        super().__init__(database_path)
        self.failed_owner_completion_updates = 0

    @contextmanager
    def transaction(self, *, immediate: bool = False):
        with super().transaction(immediate=immediate) as connection:
            yield FailingOwnerCompletionConnection(connection, self)


def test_staging_cut_leaves_only_orphan_cas_bytes_and_no_public_database_authority(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "metadata.sqlite")
    try:
        _seed_eligible_publication_inputs(store)
        _running_publish_job(store)
        repository = SQLitePublicationRepository(store, FixedClock())
        artifacts = FailSecondStageStore(LocalArtifactStore(tmp_path / "cas"))

        outcome = PublicationService(repository, artifacts).publish(_command())

        assert outcome.publication is not None
        assert outcome.publication.state is PublicationState.FAILED_RETRYABLE
        assert outcome.publication.failure_kind is PublicationFailureKind.ARTIFACT_IO
        assert outcome.publication.prepared_orphan_hashes == (artifacts.staged[0].content_hash,)
        assert artifacts.delegate.read_bytes(artifacts.staged[0].cas_uri)
        assert store.fetch_one("SELECT COUNT(*) AS count FROM artifacts")["count"] == 0
        assert store.fetch_one("SELECT COUNT(*) AS count FROM artifact_links")["count"] == 0
        assert store.fetch_one("SELECT COUNT(*) AS count FROM registry_entries")["count"] == 0
        assert repository.list_published() == ()
        assert sorted(
            row["event_kind"]
            for row in store.fetch_all("SELECT event_kind FROM publication_owner_events")
        ) == ["ACQUIRED", "FAILED_ATTEMPT"]
    finally:
        store.close()


def test_database_commit_cut_rolls_back_links_registry_and_published_transition(
    tmp_path: Path,
) -> None:
    store = FailingOwnerCompletionStore(tmp_path / "metadata.sqlite")
    try:
        _seed_eligible_publication_inputs(store)
        _running_publish_job(store)
        repository = SQLitePublicationRepository(store, FixedClock())
        artifacts = LocalArtifactStore(tmp_path / "cas")

        outcome = PublicationService(repository, artifacts).publish(_command())

        assert store.failed_owner_completion_updates == 1
        assert outcome.publication is not None
        assert outcome.publication.state is PublicationState.FAILED_RETRYABLE
        assert outcome.publication.failure_kind is PublicationFailureKind.STORAGE_COMMIT
        assert len(outcome.publication.prepared_orphan_hashes) == 2
        assert all(
            artifacts.read_bytes(content_hash)
            for content_hash in outcome.publication.prepared_orphan_hashes
        )
        assert store.fetch_one("SELECT COUNT(*) AS count FROM artifacts")["count"] == 0
        assert store.fetch_one("SELECT COUNT(*) AS count FROM artifact_links")["count"] == 0
        assert store.fetch_one("SELECT COUNT(*) AS count FROM registry_entries")["count"] == 0
        assert repository.list_published() == ()
        job = SQLiteJobRepository(store, FixedClock()).get_job("publish-job")
        assert job is not None
        assert job.status is JobStatus.RUNNING
    finally:
        store.close()


def test_restart_releases_preparing_publication_owner_to_retryable_state(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "metadata.sqlite")
    try:
        _seed_eligible_publication_inputs(store)
        jobs = _running_publish_job(store)
        repository = SQLitePublicationRepository(store, FixedClock())
        command = _command()
        available = repository.get_or_create_available(command)
        preparing = repository.acquire(
            publication=available,
            owner_job_id=command.owner_job_id,
            owner_token=command.owner_token,
            owner_expires_at=command.owner_expires_at,
        )
        assert preparing is not None
        prepared = repository.record_staged_artifacts(
            publication=preparing,
            orphan_hashes=(_hash("n"), _hash("o")),
        )
        assert prepared.state is PublicationState.PREPARING

        recovered = PersistentSingleWorkerJobService(jobs).startup()

        assert tuple(job.job_id for job in recovered) == ("publish-job",)
        assert recovered[0].status is JobStatus.FAILED
        assert recovered[0].error is not None
        assert recovered[0].error.code is ErrorCode.JOB_INTERRUPTED
        released = repository.get_publication(command.publication_id)
        assert released is not None
        assert released.state is PublicationState.FAILED_RETRYABLE
        assert released.failure_kind is PublicationFailureKind.OWNER_INTERRUPTED
        assert released.owner_job_id is None
        assert released.owner_token is None
        assert released.prepared_orphan_hashes == (_hash("n"), _hash("o"))
        assert sorted(
            row["event_kind"]
            for row in store.fetch_all("SELECT event_kind FROM publication_owner_events")
        ) == ["ACQUIRED", "FAILED_ATTEMPT", "RELEASED_STALE"]

        retry_jobs = _running_publish_job(store, "retry-job")
        retry = PublicationService(repository, LocalArtifactStore(tmp_path / "cas")).publish(
            _command(
                owner_job_id="retry-job",
                owner_token="retry-owner-token",
                registry_entry_id="registry-1",
            )
        )
        assert retry.publication is not None
        assert retry.publication.state is PublicationState.PUBLISHED
        retry_job = retry_jobs.get_job("retry-job")
        assert retry_job is not None
        assert retry_job.status is JobStatus.SUCCEEDED
    finally:
        store.close()
