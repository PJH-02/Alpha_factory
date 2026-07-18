from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from alpha_foundry.application.jobs import SQLiteJobRepository
from alpha_foundry.application.publication import (
    PublicationCommand,
    PublicationService,
    SQLitePublicationRepository,
)
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
from alpha_foundry.publication import (
    Publication,
    PublicationArtifact,
    PublicationFailureKind,
    PublicationRejection,
    PublicationRejectionReason,
    PublicationState,
    PublishedStrategy,
    RegistryEntry,
    StagedPublicationArtifacts,
)
from alpha_foundry.reporting import canonical_research_report_json, render_research_report_html
from alpha_foundry.validation.disclosure import HoldoutDisclosure, HoldoutDisclosurePolicy

_NOW = datetime(2026, 7, 12, 9, 30, tzinfo=UTC)


def _digest(number: int) -> str:
    return f"sha256:{number:064x}"


def _profile() -> ValidationProfile:
    return ValidationProfile(
        validation_profile_id="profile-1",
        domain=Domain.FACTOR,
        version="v1",
        profile_hash=_digest(1),
        hard_gates=(
            ValidationGate(
                gate_id="quality",
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
        holdout_policy_hash=_digest(2),
        disclosure_policy_hash=_digest(3),
        allowed_disclosure_fields=("sharpe",),
    )


def _command() -> PublicationCommand:
    disclosure_metric = DisclosedMetric(
        name="sharpe",
        value=Decimal("1.25"),
        threshold=Decimal("1"),
        decision=Decision.PASS,
    )
    report = ResearchReport(
        publication_id="publication-1",
        strategy_id="strategy-1",
        domain=Domain.FACTOR,
        evidence_ids=("validation-1", "pbo-certificate-1"),
        provider_attempt_ids=("provider-attempt-1",),
        dataset_hashes=(_digest(4),),
        config_hash=_digest(5),
        code_hash=_digest(6),
        schema_hashes=(_digest(7),),
        lineage_id="lineage-1",
        validation_decision=Decision.PASS,
        disclosed_metrics=(disclosure_metric,),
        artifact_hashes=(_digest(8),),
        created_at=_NOW,
    )
    profile = _profile()
    policy = HoldoutDisclosurePolicy(
        policy_id="disclosure-policy-1",
        version="v1",
        policy_hash=profile.disclosure_policy_hash,
        allowed_disclosure_fields=("sharpe",),
    )
    command = PublicationCommand(
        publication_id="publication-1",
        validation_id="validation-1",
        strategy_id="strategy-1",
        candidate_hash=_digest(9),
        lineage_id="lineage-1",
        registry_entry_id="registry-entry-1",
        owner_job_id="publish-job-1",
        owner_token="owner-token-1",
        owner_expires_at=_NOW + timedelta(minutes=5),
        validation_profile=profile,
        validation_decision=Decision.PASS,
        validation_complete=True,
        lineage_closed=True,
        holdout_complete=True,
        disclosure=HoldoutDisclosure(
            decision=Decision.PASS,
            disclosed_metrics=(disclosure_metric,),
        ),
        disclosure_policy=policy,
        report=report,
        input_hash=_digest(10),
    )
    return replace(command, input_hash=command.expected_input_hash)


class FailingArtifactStore:
    def __init__(self, root: Path, *, fail_on_put: int | None) -> None:
        self._store = LocalArtifactStore(root)
        self.fail_on_put = fail_on_put
        self.put_calls = 0

    def put_bytes(self, payload: bytes, media_type: str) -> StoredArtifact:
        self.put_calls += 1
        if self.put_calls == self.fail_on_put:
            raise OSError("simulated artifact staging interruption")
        return self._store.put_bytes(payload, media_type)

    def read_bytes(self, reference: str) -> bytes:
        return self._store.read_bytes(reference)


class PublicationRepositoryFake:
    """Stateful fake that exposes a registry and report links only at atomic publish."""

    def __init__(self) -> None:
        self.publication: Publication | None = None
        self.states: list[PublicationState] = []
        self.registry: PublishedStrategy | None = None
        self.artifacts: dict[str, PublicationArtifact] = {}
        self.registry_entry_id: str | None = None
        self.rejections: dict[tuple[str, str], PublicationRejection] = {}

    def preflight_reason(self, command: PublicationCommand) -> PublicationRejectionReason | None:
        return None

    def record_rejection(
        self,
        *,
        validation_id: str,
        input_hash: str,
        reason: PublicationRejectionReason,
    ) -> PublicationRejection:
        key = (validation_id, input_hash)
        if key not in self.rejections:
            self.rejections[key] = PublicationRejection(
                publication_rejection_id=f"rejection-{len(self.rejections) + 1}",
                validation_id=validation_id,
                input_hash=input_hash,
                reason=reason,
                created_at=_NOW,
            )
        return self.rejections[key]

    def get_or_create_available(self, command: PublicationCommand) -> Publication:
        if self.publication is None:
            self.publication = Publication(
                publication_id=command.publication_id,
                validation_id=command.validation_id,
                strategy_id=command.strategy_id,
                candidate_hash=command.candidate_hash,
                lineage_id=command.lineage_id,
                profile_hash=command.validation_profile.profile_hash,
                state=PublicationState.AVAILABLE,
                owner_token=None,
                owner_job_id=None,
                owner_epoch=0,
                owner_expires_at=None,
                attempt_count=0,
                row_version=1,
                created_at=_NOW,
                updated_at=_NOW,
            )
            self.states.append(self.publication.state)
        if self.registry_entry_id is None:
            self.registry_entry_id = command.registry_entry_id
        elif self.registry_entry_id != command.registry_entry_id:
            raise AssertionError("publication retry changed its pinned registry entry identity")
        if self.publication.profile_hash != command.validation_profile.profile_hash:
            raise AssertionError("publication retry changed its pinned profile identity")
        return self.publication

    def get_publication(self, publication_id: str) -> Publication | None:
        if self.publication is not None and self.publication.publication_id == publication_id:
            return self.publication
        return None

    def acquire(
        self,
        *,
        publication: Publication,
        owner_job_id: str,
        owner_token: str,
        owner_expires_at: datetime,
    ) -> Publication | None:
        current = self._current(publication)
        if (
            current.state not in {PublicationState.AVAILABLE, PublicationState.FAILED_RETRYABLE}
            or current.row_version != publication.row_version
        ):
            return None
        self.publication = replace(
            current,
            state=PublicationState.PREPARING,
            owner_token=owner_token,
            owner_job_id=owner_job_id,
            owner_epoch=current.owner_epoch + 1,
            owner_expires_at=owner_expires_at,
            attempt_count=current.attempt_count + 1,
            row_version=current.row_version + 1,
            updated_at=_NOW,
        )
        self.states.append(self.publication.state)
        return self.publication

    def record_staged_artifacts(
        self, *, publication: Publication, orphan_hashes: tuple[str, ...]
    ) -> Publication:
        current = self._owned_preparing(publication)
        self.publication = replace(
            current,
            prepared_orphan_hashes=orphan_hashes,
            row_version=current.row_version + 1,
            updated_at=_NOW,
        )
        return self.publication

    def fail_attempt(
        self,
        *,
        publication: Publication,
        failure_kind: PublicationFailureKind,
        orphan_hashes: tuple[str, ...],
    ) -> Publication:
        current = self._current(publication)
        if current.state is PublicationState.PUBLISHED:
            return current
        current = self._owned_preparing(publication)
        self.publication = replace(
            current,
            state=PublicationState.FAILED_RETRYABLE,
            owner_token=None,
            owner_job_id=None,
            owner_expires_at=None,
            prepared_orphan_hashes=orphan_hashes,
            failure_kind=failure_kind,
            row_version=current.row_version + 1,
            updated_at=_NOW,
        )
        self.states.append(self.publication.state)
        return self.publication

    def publish_atomically(
        self,
        *,
        publication: Publication,
        registry_entry_id: str,
        artifacts: StagedPublicationArtifacts,
    ) -> Publication:
        current = self._owned_preparing(publication)
        if current.owner_expires_at is None or current.owner_expires_at.astimezone(UTC) <= _NOW:
            raise AssertionError("publication commit lost its active owner lease")
        if self.registry_entry_id != registry_entry_id:
            raise AssertionError("publication commit changed its pinned registry entry identity")
        if current.prepared_orphan_hashes != artifacts.orphan_hashes:
            raise AssertionError("publication commit changed its staged artifact graph")
        self.registry = PublishedStrategy(
            registry_entry=RegistryEntry(
                registry_entry_id=registry_entry_id,
                publication_id=current.publication_id,
                validation_id=current.validation_id,
                strategy_id=current.strategy_id,
                candidate_hash=current.candidate_hash,
                lineage_id=current.lineage_id,
                created_at=_NOW,
            ),
            published_at=_NOW,
        )
        self.artifacts = {
            "RESEARCH_REPORT_JSON": artifacts.json_report,
            "RESEARCH_REPORT_HTML": artifacts.html_report,
        }
        self.publication = replace(
            current,
            state=PublicationState.PUBLISHED,
            owner_token=None,
            owner_job_id=None,
            owner_expires_at=None,
            prepared_orphan_hashes=(),
            failure_kind=None,
            published_at=_NOW,
            row_version=current.row_version + 1,
            updated_at=_NOW,
        )
        self.states.append(self.publication.state)
        return self.publication

    def list_published(self) -> tuple[PublishedStrategy, ...]:
        return (
            (self.registry,)
            if self.publication is not None
            and self.publication.state is PublicationState.PUBLISHED
            and self.registry is not None
            else ()
        )

    def get_published(self, publication_id: str) -> PublishedStrategy | None:
        if (
            self.publication is not None
            and self.publication.state is PublicationState.PUBLISHED
            and self.registry is not None
            and self.registry.registry_entry.publication_id == publication_id
        ):
            return self.registry
        return None

    def get_published_artifact(self, publication_id: str, role: str) -> PublicationArtifact | None:
        if self.get_published(publication_id) is None:
            return None
        return self.artifacts.get(role)

    def list_rejections(self) -> tuple[PublicationRejection, ...]:
        return tuple(self.rejections.values())

    def get_rejection(self, publication_rejection_id: str) -> PublicationRejection | None:
        return next(
            (
                rejection
                for rejection in self.rejections.values()
                if rejection.publication_rejection_id == publication_rejection_id
            ),
            None,
        )

    def _current(self, publication: Publication) -> Publication:
        if (
            self.publication is None
            or self.publication.publication_id != publication.publication_id
        ):
            raise AssertionError("publication is not present")
        return self.publication

    def _owned_preparing(self, publication: Publication) -> Publication:
        current = self._current(publication)
        if (
            current.state is not PublicationState.PREPARING
            or current.owner_token != publication.owner_token
            or current.owner_job_id != publication.owner_job_id
            or current.owner_epoch != publication.owner_epoch
            or current.row_version != publication.row_version
        ):
            raise AssertionError("publication owner compare-and-swap was not preserved")
        return current


def test_it_pub_001_retryable_artifact_failure_has_no_public_partial_and_retries_idempotently(
    tmp_path: Path,
) -> None:
    command = _command()
    repository = PublicationRepositoryFake()
    artifacts = FailingArtifactStore(tmp_path / "artifacts", fail_on_put=2)
    service = PublicationService(repository, artifacts)

    failed = service.publish(command)

    assert failed.publication is not None
    assert failed.publication.state is PublicationState.FAILED_RETRYABLE
    assert failed.publication.failure_kind is PublicationFailureKind.ARTIFACT_IO
    assert len(failed.publication.prepared_orphan_hashes) == 1
    assert repository.states == [
        PublicationState.AVAILABLE,
        PublicationState.PREPARING,
        PublicationState.FAILED_RETRYABLE,
    ]
    assert service.list_published() == ()
    assert service.get_published(command.publication_id) is None
    assert service.report(command.publication_id) is None
    assert service.report_html(command.publication_id) is None

    artifacts.fail_on_put = None
    published = service.publish(command)
    repeat = service.publish(command)

    assert published.publication is not None
    assert published.publication.state is PublicationState.PUBLISHED
    assert published.publication.profile_hash == command.validation_profile.profile_hash
    assert repeat.publication == published.publication
    assert repository.states == [
        PublicationState.AVAILABLE,
        PublicationState.PREPARING,
        PublicationState.FAILED_RETRYABLE,
        PublicationState.PREPARING,
        PublicationState.PUBLISHED,
    ]
    assert published.publication.attempt_count == 2
    assert artifacts.put_calls == 4
    assert len(service.list_published()) == 1
    assert service.get_published(command.publication_id) is not None
    assert service.report(command.publication_id) == command.report
    assert service.report_html(command.publication_id) is not None


def test_it_pub_002_preparing_acquisition_race_stays_non_public_without_staging(
    tmp_path: Path,
) -> None:
    command = _command()
    repository = PublicationRepositoryFake()
    artifacts = FailingArtifactStore(tmp_path / "artifacts", fail_on_put=None)
    service = PublicationService(repository, artifacts)
    available = repository.get_or_create_available(command)
    preparing = repository.acquire(
        publication=available,
        owner_job_id="other-publish-job",
        owner_token="other-owner-token",
        owner_expires_at=_NOW + timedelta(minutes=5),
    )

    result = service.publish(command)

    assert preparing is not None
    assert result.publication == preparing
    assert result.publication is not None
    assert result.publication.state is PublicationState.PREPARING
    assert artifacts.put_calls == 0
    assert service.list_published() == ()
    assert service.get_published(command.publication_id) is None
    assert service.report(command.publication_id) is None
    assert service.report_html(command.publication_id) is None


@dataclass(frozen=True)
class FixedClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


def _seed_publish_prerequisites(store: SQLiteStore, command: PublicationCommand) -> None:
    timestamp = _NOW.isoformat(timespec="microseconds").replace("+00:00", "Z")
    with store.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO lineages (
                lineage_id, status, closed_at, close_reason, root_refs_json, created_at
            ) VALUES (?, 'CLOSED', ?, 'HOLDOUT_CONSUMED', '[]', ?)
            """,
            (command.lineage_id, timestamp, timestamp),
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
                command.lineage_id,
                _digest(11),
                _digest(12),
                command.validation_profile.profile_hash,
                command.candidate_hash,
                timestamp,
                timestamp,
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
                "pbo-certificate-1",
                "search-run-1",
                command.validation_profile.profile_hash,
                timestamp,
                "test",
                "v1",
                "v1",
            ),
        )
        connection.execute(
            """
            INSERT INTO validations (
                validation_id, strategy_id, candidate_hash, lineage_id, profile_hash,
                pre_validation_decision, gate_summary_json, fold_summary_json, pbo_certificate_id,
                content_hash, created_at, created_by, schema_version, code_version
            ) VALUES (?, ?, ?, ?, ?, 'PASS', '[]', '[]', ?, ?, ?, 'test', 'v1', 'v1')
            """,
            (
                command.validation_id,
                command.strategy_id,
                command.candidate_hash,
                command.lineage_id,
                command.validation_profile.profile_hash,
                "pbo-certificate-1",
                _digest(91),
                timestamp,
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
                command.lineage_id,
                command.validation_profile.profile_hash,
                timestamp,
                command.validation_id,
                _digest(13),
            ),
        )


def test_it_pub_003_sqlite_public_views_hide_staged_artifacts_until_atomic_publish(
    tmp_path: Path,
) -> None:
    command = _command()
    store = SQLiteStore(tmp_path / "metadata.sqlite")
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    try:
        _seed_publish_prerequisites(store, command)
        clock = FixedClock(_NOW)
        jobs = SQLiteJobRepository(store, clock)
        jobs.create_job(
            job_id=command.owner_job_id,
            kind=JobKind.PUBLISH,
            resource_id=command.publication_id,
            command_fingerprint="publish-command-1",
            stage="PUBLISH",
        )
        running = jobs.claim_next_queued_job()
        assert running is not None
        assert running.status is JobStatus.RUNNING

        repository = SQLitePublicationRepository(store, clock)
        service = PublicationService(repository, artifacts)
        available = repository.get_or_create_available(command)
        acquired = repository.acquire(
            publication=available,
            owner_job_id=command.owner_job_id,
            owner_token=command.owner_token,
            owner_expires_at=command.owner_expires_at,
        )
        assert acquired is not None
        json_metadata = artifacts.put_bytes(
            canonical_research_report_json(command.report),
            "application/json",
        )
        html_metadata = artifacts.put_bytes(
            render_research_report_html(command.report),
            "text/html",
        )
        staged = StagedPublicationArtifacts(
            json_report=PublicationArtifact(
                cas_uri=json_metadata.cas_uri,
                content_hash=json_metadata.content_hash,
                byte_size=json_metadata.byte_size,
                media_type=json_metadata.media_type,
            ),
            html_report=PublicationArtifact(
                cas_uri=html_metadata.cas_uri,
                content_hash=html_metadata.content_hash,
                byte_size=html_metadata.byte_size,
                media_type=html_metadata.media_type,
            ),
        )
        prepared = repository.record_staged_artifacts(
            publication=acquired,
            orphan_hashes=staged.orphan_hashes,
        )

        assert prepared.state is PublicationState.PREPARING
        assert repository.list_published() == ()
        assert repository.get_published(command.publication_id) is None
        assert (
            repository.get_published_artifact(command.publication_id, "RESEARCH_REPORT_JSON")
            is None
        )
        assert service.report(command.publication_id) is None
        assert service.report_html(command.publication_id) is None

        published = repository.publish_atomically(
            publication=prepared,
            registry_entry_id=command.registry_entry_id,
            artifacts=staged,
        )

        assert published.state is PublicationState.PUBLISHED
        assert len(repository.list_published()) == 1
        assert repository.get_published(command.publication_id) is not None
        assert (
            repository.get_published_artifact(command.publication_id, "RESEARCH_REPORT_JSON")
            is not None
        )
        assert (
            repository.get_published_artifact(command.publication_id, "RESEARCH_REPORT_HTML")
            is not None
        )
        assert service.report(command.publication_id) == command.report
        assert service.report_html(command.publication_id) == render_research_report_html(
            command.report
        )
        completed = jobs.get_job(command.owner_job_id)
        assert completed is not None
        assert completed.status is JobStatus.SUCCEEDED
    finally:
        store.close()
