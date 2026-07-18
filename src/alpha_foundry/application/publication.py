"""Application service for atomic, PUBLISHED-only report publication.

The service stages immutable report bytes outside a database transaction.  Its repository
callback owns the one short transaction that links both artifacts, creates the pass
registry entry, changes the publication state, and completes the exact owner job.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import uuid4

from alpha_foundry.application._timestamps import format_utc_timestamp, parse_utc_timestamp
from alpha_foundry.application.ports import ArtifactMetadata, Clock
from alpha_foundry.domain.canonical import digest
from alpha_foundry.domain.models import Decision, ResearchReport, ValidationProfile
from alpha_foundry.infrastructure.artifacts import ArtifactStoreError
from alpha_foundry.infrastructure.db import SQLiteStore
from alpha_foundry.publication import (
    Publication,
    PublicationArtifact,
    PublicationFailureKind,
    PublicationOwnerEventKind,
    PublicationRejection,
    PublicationRejectionReason,
    PublicationState,
    PublicationStateError,
    PublishedStrategy,
    RegistryEntry,
    StagedPublicationArtifacts,
)
from alpha_foundry.reporting import (
    _HTML_MEDIA_TYPE,
    _JSON_MEDIA_TYPE,
    canonical_research_report_json,
    parse_canonical_research_report,
    render_research_report_html,
)
from alpha_foundry.validation.disclosure import (
    DisclosurePolicyError,
    HoldoutDisclosure,
    HoldoutDisclosurePolicy,
)


class PublicationArtifactStorePort(Protocol):
    """Content-addressed storage used for non-authoritative report staging."""

    def put_bytes(self, payload: bytes, media_type: str) -> ArtifactMetadata:
        """Atomically persist bytes and return verified content-addressed metadata."""

    def read_bytes(self, reference: str) -> bytes:
        """Read and integrity-check an immutable payload by CAS reference."""


@dataclass(frozen=True, slots=True)
class PublicationCommand:
    """Trusted, fully pinned input for one publication attempt.

    ``input_hash`` is supplied by the validation/publication hand-off and must equal
    :attr:`expected_input_hash`; it protects against mixing report bytes with a
    different validation, lineage, candidate, or frozen profile.
    """

    publication_id: str
    validation_id: str
    strategy_id: str
    candidate_hash: str
    lineage_id: str
    registry_entry_id: str
    owner_job_id: str
    owner_token: str
    owner_expires_at: datetime
    validation_profile: ValidationProfile
    validation_decision: Decision
    validation_complete: bool
    lineage_closed: bool
    holdout_complete: bool
    disclosure: HoldoutDisclosure
    disclosure_policy: HoldoutDisclosurePolicy
    report: ResearchReport
    input_hash: str

    def __post_init__(self) -> None:
        required = (
            self.publication_id,
            self.validation_id,
            self.strategy_id,
            self.candidate_hash,
            self.lineage_id,
            self.registry_entry_id,
            self.owner_job_id,
            self.owner_token,
            self.input_hash,
        )
        if any(not value.strip() for value in required):
            raise ValueError("publication command identifiers must not be blank")
        if self.owner_expires_at.tzinfo is None or self.owner_expires_at.utcoffset() is None:
            raise ValueError("publication owner expiry must be timezone-aware")
        if not _is_display_digest(self.input_hash):
            raise ValueError("publication input_hash must be a SHA-256 display digest")

    @property
    def expected_input_hash(self) -> str:
        """Compute the exact canonical hand-off identity without mutable metadata."""
        return publication_input_hash(
            validation_id=self.validation_id,
            strategy_id=self.strategy_id,
            candidate_hash=self.candidate_hash,
            lineage_id=self.lineage_id,
            profile_hash=self.validation_profile.profile_hash,
            disclosure_policy_hash=self.disclosure_policy.policy_hash,
            validation_decision=self.validation_decision,
            validation_complete=self.validation_complete,
            lineage_closed=self.lineage_closed,
            holdout_complete=self.holdout_complete,
            holdout_decision=self.disclosure.decision,
            report=self.report,
        )


@dataclass(frozen=True, slots=True)
class PublicationResult:
    """Observable outcome of one call, without exposing rejected work publicly."""

    publication: Publication | None
    rejection: PublicationRejection | None = None

    def __post_init__(self) -> None:
        if self.publication is None and self.rejection is None:
            raise ValueError("publication result requires a publication or rejection")
        if self.publication is not None and self.rejection is not None:
            raise ValueError("publication result cannot be both published work and a rejection")

    @property
    def is_rejected(self) -> bool:
        return self.rejection is not None


class PublicationRepositoryPort(Protocol):
    """Durable publication authority; implementations own all SQL transactions."""

    def preflight_reason(self, command: PublicationCommand) -> PublicationRejectionReason | None:
        """Verify persisted non-sealed validation, lineage, and holdout prerequisites."""

    def record_rejection(
        self,
        *,
        validation_id: str,
        input_hash: str,
        reason: PublicationRejectionReason,
    ) -> PublicationRejection:
        """Append or return the idempotent internal preflight rejection."""

    def get_or_create_available(self, command: PublicationCommand) -> Publication:
        """Insert the unique eligible AVAILABLE row or return its immutable identity."""

    def get_publication(self, publication_id: str) -> Publication | None:
        """Return an internal lifecycle snapshot by publication identity."""

    def acquire(
        self,
        *,
        publication: Publication,
        owner_job_id: str,
        owner_token: str,
        owner_expires_at: datetime,
    ) -> Publication | None:
        """CAS AVAILABLE/FAILED_RETRYABLE to PREPARING and append ACQUIRED."""

    def record_staged_artifacts(
        self, *, publication: Publication, orphan_hashes: tuple[str, ...]
    ) -> Publication:
        """Durably retain staged hashes under the matching live PREPARING owner."""

    def fail_attempt(
        self,
        *,
        publication: Publication,
        failure_kind: PublicationFailureKind,
        orphan_hashes: tuple[str, ...],
    ) -> Publication:
        """CAS a matching PREPARING owner to ownerless FAILED_RETRYABLE."""

    def publish_atomically(
        self,
        *,
        publication: Publication,
        registry_entry_id: str,
        artifacts: StagedPublicationArtifacts,
    ) -> Publication:
        """Commit links, registry, PUBLISHED event/state, and owner job success together."""

    def list_published(self) -> tuple[PublishedStrategy, ...]:
        """Return the fixed PUBLISHED-only researcher view."""

    def get_published(self, publication_id: str) -> PublishedStrategy | None:
        """Return one PUBLISHED-only researcher view row."""

    def get_published_artifact(self, publication_id: str, role: str) -> PublicationArtifact | None:
        """Return a report artifact only when its owning publication is PUBLISHED."""

    def list_rejections(self) -> tuple[PublicationRejection, ...]:
        """Return internal preflight rejections, never a researcher-facing result."""

    def get_rejection(self, publication_rejection_id: str) -> PublicationRejection | None:
        """Return one internal preflight rejection by identity."""


class PublicationStorageError(RuntimeError):
    """A publication artifact or atomic commit could not be durably completed."""


class PublicationService:
    """Preflight, stage, and atomically publish only complete pass results."""

    def __init__(
        self,
        repository: PublicationRepositoryPort,
        artifact_store: PublicationArtifactStorePort,
    ) -> None:
        self._repository = repository
        self._artifact_store = artifact_store

    def publish(self, command: PublicationCommand) -> PublicationResult:
        """Perform one idempotent publication attempt without leaking intermediate state."""
        rejection_reason = self._preflight_reason(command)
        if rejection_reason is None:
            rejection_reason = self._repository.preflight_reason(command)
        if rejection_reason is not None:
            return PublicationResult(
                publication=None,
                rejection=self._repository.record_rejection(
                    validation_id=command.validation_id,
                    input_hash=command.input_hash,
                    reason=rejection_reason,
                ),
            )

        publication = self._repository.get_or_create_available(command)
        if publication.state is PublicationState.PUBLISHED:
            return PublicationResult(publication=publication)

        acquired = self._repository.acquire(
            publication=publication,
            owner_job_id=command.owner_job_id,
            owner_token=command.owner_token,
            owner_expires_at=command.owner_expires_at,
        )
        if acquired is None:
            current = self._repository.get_publication(command.publication_id)
            if current is None:
                raise PublicationStateError("publication disappeared after an acquisition race")
            return PublicationResult(publication=current)

        try:
            json_payload = canonical_research_report_json(command.report)
            html_payload = render_research_report_html(command.report)
        except OSError:
            failed = self._repository.fail_attempt(
                publication=acquired,
                failure_kind=PublicationFailureKind.REPORT_RENDER,
                orphan_hashes=(),
            )
            return PublicationResult(publication=failed)

        prepared = acquired
        json_artifact: PublicationArtifact | None = None
        html_artifact: PublicationArtifact | None = None
        try:
            json_artifact = self._stage_artifact(json_payload, _JSON_MEDIA_TYPE)
            prepared = self._repository.record_staged_artifacts(
                publication=prepared,
                orphan_hashes=(json_artifact.content_hash,),
            )
            html_artifact = self._stage_artifact(html_payload, _HTML_MEDIA_TYPE)
            artifacts = StagedPublicationArtifacts(
                json_report=json_artifact,
                html_report=html_artifact,
            )
            prepared = self._repository.record_staged_artifacts(
                publication=prepared,
                orphan_hashes=artifacts.orphan_hashes,
            )
            if prepared.state is not PublicationState.PREPARING:
                raise PublicationStateError("staged publication lost its owner state")
        except ArtifactStoreError:
            raise
        except OSError:
            failed = self._repository.fail_attempt(
                publication=prepared,
                failure_kind=PublicationFailureKind.ARTIFACT_IO,
                orphan_hashes=_staged_hashes(json_artifact) + _staged_hashes(html_artifact),
            )
            return PublicationResult(publication=failed)
        except sqlite3.OperationalError:
            failed = self._repository.fail_attempt(
                publication=prepared,
                failure_kind=PublicationFailureKind.STORAGE_COMMIT,
                orphan_hashes=_staged_hashes(json_artifact) + _staged_hashes(html_artifact),
            )
            return PublicationResult(publication=failed)

        try:
            published = self._repository.publish_atomically(
                publication=prepared,
                registry_entry_id=command.registry_entry_id,
                artifacts=artifacts,
            )
        except sqlite3.OperationalError:
            failed = self._repository.fail_attempt(
                publication=prepared,
                failure_kind=PublicationFailureKind.STORAGE_COMMIT,
                orphan_hashes=artifacts.orphan_hashes,
            )
            return PublicationResult(publication=failed)
        if published.state is not PublicationState.PUBLISHED:
            raise PublicationStateError("atomic publication callback did not return PUBLISHED")
        return PublicationResult(publication=published)

    def list_published(self) -> tuple[PublishedStrategy, ...]:
        """Return the fixed public registry; callers cannot request another state."""
        return self._repository.list_published()

    def get_published(self, publication_id: str) -> PublishedStrategy | None:
        """Return one public registry entry only when it is PUBLISHED."""
        return self._repository.get_published(publication_id)

    def report(self, publication_id: str) -> ResearchReport | None:
        """Read the canonical JSON authority for one PUBLISHED publication."""
        artifact = self._repository.get_published_artifact(publication_id, "RESEARCH_REPORT_JSON")
        if artifact is None:
            return None
        try:
            report = parse_canonical_research_report(
                self._artifact_store.read_bytes(artifact.cas_uri)
            )
        except (OSError, ValueError, UnicodeError) as error:
            raise PublicationStorageError("published JSON report artifact is invalid") from error
        if (
            report.publication_id != publication_id
            or report.validation_decision is not Decision.PASS
        ):
            raise PublicationStorageError("published JSON report has inconsistent public identity")
        return report

    def report_html(self, publication_id: str) -> bytes | None:
        """Return the staged HTML only after verifying it is the canonical rendering."""
        report = self.report(publication_id)
        if report is None:
            return None
        artifact = self._repository.get_published_artifact(publication_id, "RESEARCH_REPORT_HTML")
        if artifact is None:
            raise PublicationStorageError("published report is missing its HTML artifact")
        expected = render_research_report_html(report)
        try:
            payload = self._artifact_store.read_bytes(artifact.cas_uri)
        except OSError as error:
            raise PublicationStorageError(
                "published HTML report artifact is unavailable"
            ) from error
        if payload != expected:
            raise PublicationStorageError(
                "published HTML report is not the canonical escaped rendering"
            )
        return payload

    def list_rejections(self) -> tuple[PublicationRejection, ...]:
        """Return internal-only preflight rejections."""
        return self._repository.list_rejections()

    def get_rejection(self, publication_rejection_id: str) -> PublicationRejection | None:
        """Return one internal-only preflight rejection."""
        return self._repository.get_rejection(publication_rejection_id)

    def _preflight_reason(self, command: PublicationCommand) -> PublicationRejectionReason | None:
        if (
            not command.validation_complete
            or not command.holdout_complete
            or command.validation_decision is not Decision.PASS
            or command.disclosure.decision is not Decision.PASS
            or command.report.validation_decision is not Decision.PASS
            or any(
                metric.decision is not Decision.PASS
                for metric in command.disclosure.disclosed_metrics
            )
        ):
            return PublicationRejectionReason.VALIDATION_NOT_PASS
        if not command.lineage_closed:
            return PublicationRejectionReason.LINEAGE_NOT_CLOSED
        try:
            command.disclosure_policy.validate_profile(command.validation_profile)
            command.disclosure_policy.validate_disclosure(command.disclosure)
        except DisclosurePolicyError:
            return PublicationRejectionReason.DISCLOSURE_INVALID
        if command.report.publication_id != command.publication_id:
            return PublicationRejectionReason.INPUT_HASH_MISMATCH
        if command.report.strategy_id != command.strategy_id:
            return PublicationRejectionReason.INPUT_HASH_MISMATCH
        if command.report.domain is not command.validation_profile.domain:
            return PublicationRejectionReason.INPUT_HASH_MISMATCH
        if command.report.disclosed_metrics != command.disclosure.disclosed_metrics:
            return PublicationRejectionReason.DISCLOSURE_INVALID
        if command.input_hash != command.expected_input_hash:
            return PublicationRejectionReason.INPUT_HASH_MISMATCH
        return None

    def _stage_artifact(self, payload: bytes, media_type: str) -> PublicationArtifact:
        metadata = self._artifact_store.put_bytes(payload, media_type)
        expected_hash = f"sha256:{sha256(payload).hexdigest()}"
        expected_uri = f"cas://sha256/{expected_hash.removeprefix('sha256:')}"
        if (
            not isinstance(metadata.cas_uri, str)
            or not isinstance(metadata.content_hash, str)
            or not isinstance(metadata.byte_size, int)
            or isinstance(metadata.byte_size, bool)
            or not isinstance(metadata.media_type, str)
            or metadata.cas_uri != expected_uri
            or metadata.content_hash != expected_hash
            or metadata.byte_size != len(payload)
            or metadata.media_type != media_type
        ):
            raise PublicationStorageError(
                "artifact store returned incomplete or mismatched report metadata"
            )
        return PublicationArtifact(
            cas_uri=metadata.cas_uri,
            content_hash=metadata.content_hash,
            byte_size=metadata.byte_size,
            media_type=metadata.media_type,
        )


class SQLitePublicationRepository:
    """SQLite implementation of publication CAS, public views, and rejection retention."""

    _REPORT_JSON_ROLE = "RESEARCH_REPORT_JSON"
    _REPORT_HTML_ROLE = "RESEARCH_REPORT_HTML"
    _SCHEMA_VERSION = "research-report-v1"
    _CODE_VERSION = "publication-service-v1"

    def __init__(self, store: SQLiteStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def preflight_reason(self, command: PublicationCommand) -> PublicationRejectionReason | None:
        """Validate immutable validation/lineage/holdout facts without reading sealed data."""
        row = self._store.fetch_one(
            """
            SELECT validations.strategy_id, validations.candidate_hash, validations.lineage_id,
                   validations.profile_hash AS validation_profile_hash,
                   validations.pre_validation_decision, lineages.status AS lineage_status,
                   pbo_certificates.profile_hash AS pbo_profile_hash,
                   pbo_certificates.decision AS pbo_decision,
                   holdout_slots.profile_hash AS holdout_profile_hash,
                   holdout_slots.state AS holdout_state,
                   holdout_slots.consuming_validation_id AS consuming_validation_id
            FROM validations
            JOIN lineages ON lineages.lineage_id = validations.lineage_id
            LEFT JOIN pbo_certificates
                ON pbo_certificates.pbo_certificate_id = validations.pbo_certificate_id
            LEFT JOIN holdout_slots ON holdout_slots.lineage_id = validations.lineage_id
            WHERE validations.validation_id = ?
            """,
            (command.validation_id,),
        )
        if row is None or row["pre_validation_decision"] != Decision.PASS.value:
            return PublicationRejectionReason.VALIDATION_NOT_PASS
        if (
            row["strategy_id"] != command.strategy_id
            or row["candidate_hash"] != command.candidate_hash
            or row["lineage_id"] != command.lineage_id
            or row["validation_profile_hash"] != command.validation_profile.profile_hash
            or row["pbo_profile_hash"] != command.validation_profile.profile_hash
            or row["holdout_profile_hash"] != command.validation_profile.profile_hash
        ):
            return PublicationRejectionReason.INPUT_HASH_MISMATCH
        if row["lineage_status"] != "CLOSED":
            return PublicationRejectionReason.LINEAGE_NOT_CLOSED
        if (
            row["pbo_decision"] != Decision.PASS.value
            or row["holdout_state"] != "CONSUMED"
            or row["consuming_validation_id"] != command.validation_id
        ):
            return PublicationRejectionReason.VALIDATION_NOT_PASS
        return None

    def record_rejection(
        self,
        *,
        validation_id: str,
        input_hash: str,
        reason: PublicationRejectionReason,
    ) -> PublicationRejection:
        now = self._timestamp(self._clock.now())
        with self._store.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM publication_rejections
                WHERE validation_id = ? AND input_hash = ?
                """,
                (validation_id, input_hash),
            ).fetchone()
            if row is None:
                rejection_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO publication_rejections (
                        publication_rejection_id, validation_id, input_hash, reason_code, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (rejection_id, validation_id, input_hash, reason.value, now),
                )
                row = connection.execute(
                    "SELECT * FROM publication_rejections WHERE publication_rejection_id = ?",
                    (rejection_id,),
                ).fetchone()
            if row is None:
                raise PublicationStateError("publication rejection could not be read")
            rejection = self._rejection_from_row(row)
            if rejection.reason is not reason:
                raise PublicationStateError(
                    "preflight rejection idempotency key has a different reason"
                )
            return rejection

    def get_or_create_available(self, command: PublicationCommand) -> Publication:
        now = self._timestamp(self._clock.now())
        with self._store.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM publications WHERE validation_id = ?", (command.validation_id,)
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO publications (
                        publication_id, validation_id, strategy_id, candidate_hash, lineage_id,
                        profile_hash, registry_entry_id, state, owner_token, owner_job_id, owner_epoch,
                        owner_expires_at, attempt_count, prepared_orphan_hashes_json, failure_kind,
                        created_at, updated_at, published_at, row_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'AVAILABLE', NULL, NULL, 0, NULL, 0, NULL, NULL,
                        ?, ?, NULL, 1)
                    """,
                    (
                        command.publication_id,
                        command.validation_id,
                        command.strategy_id,
                        command.candidate_hash,
                        command.lineage_id,
                        command.validation_profile.profile_hash,
                        command.registry_entry_id,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM publications WHERE publication_id = ?", (command.publication_id,)
                ).fetchone()
            if row is None:
                raise PublicationStateError("publication could not be created")
            publication = self._publication_from_row(row)
            self._require_matching_identity(publication, command)
            persisted_registry_entry_id = row["registry_entry_id"]
            if (
                persisted_registry_entry_id is None
                and publication.state is not PublicationState.PUBLISHED
            ):
                connection.execute(
                    """
                    UPDATE publications
                    SET registry_entry_id = ?, updated_at = ?, row_version = row_version + 1
                    WHERE publication_id = ? AND registry_entry_id IS NULL AND row_version = ?
                    """,
                    (
                        command.registry_entry_id,
                        now,
                        publication.publication_id,
                        publication.row_version,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM publications WHERE publication_id = ?",
                    (publication.publication_id,),
                ).fetchone()
                if row is None:
                    raise PublicationStateError("publication registry identity could not be read")
                publication = self._publication_from_row(row)
                persisted_registry_entry_id = row["registry_entry_id"]
            if persisted_registry_entry_id != command.registry_entry_id:
                raise PublicationStateError(
                    "publication retry has a different pinned registry entry identity"
                )
            persisted_profile_hash = row["profile_hash"]
            if (
                persisted_profile_hash is None
                and publication.state is not PublicationState.PUBLISHED
            ):
                connection.execute(
                    """
                    UPDATE publications
                    SET profile_hash = ?, updated_at = ?, row_version = row_version + 1
                    WHERE publication_id = ? AND profile_hash IS NULL AND row_version = ?
                    """,
                    (
                        command.validation_profile.profile_hash,
                        now,
                        publication.publication_id,
                        publication.row_version,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM publications WHERE publication_id = ?",
                    (publication.publication_id,),
                ).fetchone()
                if row is None:
                    raise PublicationStateError("publication profile identity could not be read")
                publication = self._publication_from_row(row)
                persisted_profile_hash = row["profile_hash"]
            if persisted_profile_hash != command.validation_profile.profile_hash:
                raise PublicationStateError(
                    "publication retry has a different pinned profile identity"
                )
            return publication

    def get_publication(self, publication_id: str) -> Publication | None:
        row = self._store.fetch_one(
            "SELECT * FROM publications WHERE publication_id = ?", (publication_id,)
        )
        return self._publication_from_row(row) if row is not None else None

    def acquire(
        self,
        *,
        publication: Publication,
        owner_job_id: str,
        owner_token: str,
        owner_expires_at: datetime,
    ) -> Publication | None:
        if not owner_job_id or not owner_token:
            raise ValueError("publication owner identifiers must not be blank")
        now_value = self._clock.now()
        now = self._timestamp(now_value)
        expires_at = self._timestamp(owner_expires_at)
        if owner_expires_at.astimezone(UTC) <= now_value.astimezone(UTC):
            raise ValueError("publication owner expiry must be in the future")
        with self._store.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM publications WHERE publication_id = ?", (publication.publication_id,)
            ).fetchone()
            if row is None:
                raise PublicationStateError("publication does not exist")
            current = self._publication_from_row(row)
            if current.state is PublicationState.PREPARING:
                if current.owner_expires_at is None:
                    raise PublicationStateError("preparing publication is missing its lease expiry")
                if current.owner_expires_at.astimezone(UTC) > now_value.astimezone(UTC):
                    return None
                if current.owner_token == owner_token:
                    raise PublicationStateError(
                        "expired publication lease requires a fresh owner token"
                    )
                released = connection.execute(
                    """
                    UPDATE publications
                    SET state = 'FAILED_RETRYABLE', owner_token = NULL, owner_job_id = NULL,
                        owner_expires_at = NULL, failure_kind = 'OWNER_INTERRUPTED',
                        updated_at = ?, row_version = row_version + 1
                    WHERE publication_id = ? AND state = 'PREPARING' AND owner_token = ?
                        AND owner_job_id = ? AND owner_epoch = ? AND row_version = ?
                    """,
                    (
                        now,
                        current.publication_id,
                        current.owner_token,
                        current.owner_job_id,
                        current.owner_epoch,
                        current.row_version,
                    ),
                )
                if released.rowcount != 1:
                    raise PublicationStateError("expired publication lease could not be reclaimed")
                self._insert_owner_event(
                    connection,
                    publication_id=current.publication_id,
                    event_kind=PublicationOwnerEventKind.RELEASED_STALE,
                    owner_job_id=current.owner_job_id,
                    owner_epoch=current.owner_epoch,
                    orphan_hashes=current.prepared_orphan_hashes,
                    created_at=now,
                )
                self._insert_owner_event(
                    connection,
                    publication_id=current.publication_id,
                    event_kind=PublicationOwnerEventKind.FAILED_ATTEMPT,
                    owner_job_id=current.owner_job_id,
                    owner_epoch=current.owner_epoch,
                    orphan_hashes=current.prepared_orphan_hashes,
                    created_at=now,
                )
                row = connection.execute(
                    "SELECT * FROM publications WHERE publication_id = ?",
                    (current.publication_id,),
                ).fetchone()
                if row is None:
                    raise PublicationStateError("reclaimed publication could not be read")
                current = self._publication_from_row(row)
            elif current.state not in {
                PublicationState.AVAILABLE,
                PublicationState.FAILED_RETRYABLE,
            }:
                return None
            elif current.row_version != publication.row_version:
                return None
            owner_job = connection.execute(
                """
                SELECT job_id FROM jobs
                WHERE job_id = ? AND kind = 'PUBLISH' AND resource_id = ?
                    AND status = 'RUNNING' AND execution_owner = 'single-worker'
                """,
                (owner_job_id, current.publication_id),
            ).fetchone()
            if owner_job is None:
                raise PublicationStateError(
                    "publication owner job is not the running local worker for this resource"
                )
            updated = connection.execute(
                """
                UPDATE publications
                SET state = 'PREPARING', owner_token = ?, owner_job_id = ?,
                    owner_epoch = owner_epoch + 1, owner_expires_at = ?, attempt_count = attempt_count + 1,
                    prepared_orphan_hashes_json = NULL, failure_kind = NULL, updated_at = ?,
                    row_version = row_version + 1
                WHERE publication_id = ? AND row_version = ?
                    AND state IN ('AVAILABLE', 'FAILED_RETRYABLE')
                """,
                (
                    owner_token,
                    owner_job_id,
                    expires_at,
                    now,
                    current.publication_id,
                    current.row_version,
                ),
            )
            if updated.rowcount != 1:
                return None
            claimed_row = connection.execute(
                "SELECT * FROM publications WHERE publication_id = ?", (current.publication_id,)
            ).fetchone()
            if claimed_row is None:
                raise PublicationStateError("claimed publication could not be read")
            claimed = self._publication_from_row(claimed_row)
            self._insert_owner_event(
                connection,
                publication_id=claimed.publication_id,
                event_kind=PublicationOwnerEventKind.ACQUIRED,
                owner_job_id=claimed.owner_job_id,
                owner_epoch=claimed.owner_epoch,
                orphan_hashes=(),
                created_at=now,
            )
            return claimed

    def record_staged_artifacts(
        self, *, publication: Publication, orphan_hashes: tuple[str, ...]
    ) -> Publication:
        """Durably retain every successfully staged report hash before final commit."""
        if len(orphan_hashes) not in {1, 2}:
            raise ValueError("staged publication must contain one or two report artifact hashes")
        orphan_json = self._hashes_json(orphan_hashes)
        if orphan_json is None:
            raise ValueError("staged publication must contain report artifact hashes")
        now_value = self._clock.now()
        now = self._timestamp(now_value)
        with self._store.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM publications WHERE publication_id = ?",
                (publication.publication_id,),
            ).fetchone()
            if row is None:
                raise PublicationStateError("staged publication does not exist")
            current = self._publication_from_row(row)
            if (
                current.state is not PublicationState.PREPARING
                or current.owner_token != publication.owner_token
                or current.owner_job_id != publication.owner_job_id
                or current.owner_epoch != publication.owner_epoch
                or current.row_version != publication.row_version
                or current.owner_expires_at is None
                or current.owner_expires_at.astimezone(UTC) <= now_value.astimezone(UTC)
            ):
                raise PublicationStateError(
                    "staged artifact record lost compare-and-swap ownership"
                )
            if (
                orphan_hashes[: len(current.prepared_orphan_hashes)]
                != current.prepared_orphan_hashes
            ):
                raise PublicationStateError(
                    "staged artifact record must retain every previously staged hash"
                )
            updated = connection.execute(
                """
                UPDATE publications
                SET prepared_orphan_hashes_json = ?, updated_at = ?,
                    row_version = row_version + 1
                WHERE publication_id = ? AND state = 'PREPARING' AND owner_token = ?
                    AND owner_job_id = ? AND owner_epoch = ? AND row_version = ?
                """,
                (
                    orphan_json,
                    now,
                    current.publication_id,
                    current.owner_token,
                    current.owner_job_id,
                    current.owner_epoch,
                    current.row_version,
                ),
            )
            if updated.rowcount != 1:
                raise PublicationStateError(
                    "staged artifact record lost compare-and-swap ownership"
                )
            row = connection.execute(
                "SELECT * FROM publications WHERE publication_id = ?", (current.publication_id,)
            ).fetchone()
            if row is None:
                raise PublicationStateError("staged publication could not be read")
            return self._publication_from_row(row)

    def fail_attempt(
        self,
        *,
        publication: Publication,
        failure_kind: PublicationFailureKind,
        orphan_hashes: tuple[str, ...],
    ) -> Publication:
        if not isinstance(failure_kind, PublicationFailureKind):
            raise ValueError("publication failure kind must be declared retryable")
        now = self._timestamp(self._clock.now())
        with self._store.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM publications WHERE publication_id = ?",
                (publication.publication_id,),
            ).fetchone()
            if row is None:
                raise PublicationStateError(
                    "publication disappeared while recording a failed attempt"
                )
            current = self._publication_from_row(row)
            if current.state is PublicationState.PUBLISHED:
                return current
            if (
                current.state is not PublicationState.PREPARING
                or current.owner_token != publication.owner_token
                or current.owner_job_id != publication.owner_job_id
                or current.owner_epoch != publication.owner_epoch
                or current.row_version != publication.row_version
            ):
                raise PublicationStateError("publication failure lost compare-and-swap ownership")
            retained_hashes = tuple(
                dict.fromkeys((*current.prepared_orphan_hashes, *orphan_hashes))
            )
            orphan_json = self._hashes_json(retained_hashes)
            updated = connection.execute(
                """
                UPDATE publications
                SET state = 'FAILED_RETRYABLE', owner_token = NULL, owner_job_id = NULL,
                    owner_expires_at = NULL, prepared_orphan_hashes_json = ?, failure_kind = ?,
                    updated_at = ?, row_version = row_version + 1
                WHERE publication_id = ? AND state = 'PREPARING' AND owner_token = ?
                    AND owner_job_id = ? AND owner_epoch = ? AND row_version = ?
                """,
                (
                    orphan_json,
                    failure_kind.value,
                    now,
                    current.publication_id,
                    current.owner_token,
                    current.owner_job_id,
                    current.owner_epoch,
                    current.row_version,
                ),
            )
            if updated.rowcount != 1:
                raise PublicationStateError("publication failure lost compare-and-swap ownership")
            self._insert_owner_event(
                connection,
                publication_id=current.publication_id,
                event_kind=PublicationOwnerEventKind.FAILED_ATTEMPT,
                owner_job_id=current.owner_job_id,
                owner_epoch=current.owner_epoch,
                orphan_hashes=retained_hashes,
                created_at=now,
            )
            row = connection.execute(
                "SELECT * FROM publications WHERE publication_id = ?", (current.publication_id,)
            ).fetchone()
            if row is None:
                raise PublicationStateError("failed publication could not be read")
            return self._publication_from_row(row)

    def publish_atomically(
        self,
        *,
        publication: Publication,
        registry_entry_id: str,
        artifacts: StagedPublicationArtifacts,
    ) -> Publication:
        now_value = self._clock.now()
        now = self._timestamp(now_value)
        with self._store.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM publications
                WHERE publication_id = ? AND state = 'PREPARING' AND owner_token = ?
                    AND owner_job_id = ? AND owner_epoch = ? AND row_version = ?
                """,
                (
                    publication.publication_id,
                    publication.owner_token,
                    publication.owner_job_id,
                    publication.owner_epoch,
                    publication.row_version,
                ),
            ).fetchone()
            if row is None:
                raise PublicationStateError("publication commit lost compare-and-swap ownership")
            current = self._publication_from_row(row)
            if current.owner_expires_at is None or current.owner_expires_at.astimezone(
                UTC
            ) <= now_value.astimezone(UTC):
                raise PublicationStateError("publication lease expired before atomic commit")
            if current.owner_job_id is None:
                raise PublicationStateError("preparing publication is missing its owner job")
            if row["registry_entry_id"] != registry_entry_id:
                raise PublicationStateError(
                    "publication commit has a different pinned registry entry identity"
                )
            if not self._prerequisites_are_current(connection, current):
                raise PublicationStateError(
                    "publication prerequisites changed before atomic commit"
                )
            if current.prepared_orphan_hashes != artifacts.orphan_hashes:
                raise PublicationStateError(
                    "published artifacts do not exactly match the staged report hashes"
                )
            json_artifact_id = self._ensure_artifact(
                connection,
                artifact=artifacts.json_report,
                created_at=now,
                created_by=publication.owner_job_id or "publication-service",
            )
            html_artifact_id = self._ensure_artifact(
                connection,
                artifact=artifacts.html_report,
                created_at=now,
                created_by=publication.owner_job_id or "publication-service",
            )
            self._insert_artifact_link(
                connection,
                artifact_id=json_artifact_id,
                publication_id=current.publication_id,
                role=self._REPORT_JSON_ROLE,
                created_at=now,
            )
            self._insert_artifact_link(
                connection,
                artifact_id=html_artifact_id,
                publication_id=current.publication_id,
                role=self._REPORT_HTML_ROLE,
                created_at=now,
            )
            connection.execute(
                """
                INSERT INTO registry_entries (
                    registry_entry_id, publication_id, validation_id, strategy_id, candidate_hash,
                    lineage_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    registry_entry_id,
                    current.publication_id,
                    current.validation_id,
                    current.strategy_id,
                    current.candidate_hash,
                    current.lineage_id,
                    now,
                ),
            )
            updated = connection.execute(
                """
                UPDATE publications
                SET state = 'PUBLISHED', owner_token = NULL, owner_job_id = NULL,
                    owner_expires_at = NULL, prepared_orphan_hashes_json = NULL, failure_kind = NULL,
                    updated_at = ?, published_at = ?, row_version = row_version + 1
                WHERE publication_id = ? AND state = 'PREPARING' AND owner_token = ?
                    AND owner_job_id = ? AND owner_epoch = ? AND row_version = ?
                """,
                (
                    now,
                    now,
                    current.publication_id,
                    current.owner_token,
                    current.owner_job_id,
                    current.owner_epoch,
                    current.row_version,
                ),
            )
            if updated.rowcount != 1:
                raise PublicationStateError(
                    "publication transition lost compare-and-swap ownership"
                )
            self._insert_owner_event(
                connection,
                publication_id=current.publication_id,
                event_kind=PublicationOwnerEventKind.PUBLISHED,
                owner_job_id=current.owner_job_id,
                owner_epoch=current.owner_epoch,
                orphan_hashes=(),
                created_at=now,
            )
            job_updated = connection.execute(
                """
                UPDATE jobs
                SET status = 'SUCCEEDED', stage = 'PUBLISHED', result_ref = ?, error_json = NULL,
                    execution_owner = NULL, finished_at = ?, updated_at = ?, row_version = row_version + 1
                WHERE job_id = ? AND kind = 'PUBLISH' AND resource_id = ?
                    AND status = 'RUNNING' AND execution_owner = 'single-worker'
                """,
                (
                    artifacts.json_report.cas_uri,
                    now,
                    now,
                    current.owner_job_id,
                    current.publication_id,
                ),
            )
            if job_updated.rowcount != 1:
                raise PublicationStateError("publication owner job is not RUNNING")
            published_row = connection.execute(
                "SELECT * FROM publications WHERE publication_id = ?", (current.publication_id,)
            ).fetchone()
            if published_row is None:
                raise PublicationStateError("published publication could not be read")
            return self._publication_from_row(published_row)

    def list_published(self) -> tuple[PublishedStrategy, ...]:
        rows = self._store.fetch_all(
            """
            SELECT registry_entries.*, publications.published_at
            FROM registry_entries
            JOIN publications ON publications.publication_id = registry_entries.publication_id
            WHERE publications.state = 'PUBLISHED'
            ORDER BY publications.published_at ASC, registry_entries.publication_id ASC
            """
        )
        return tuple(self._published_strategy_from_row(row) for row in rows)

    def get_published(self, publication_id: str) -> PublishedStrategy | None:
        row = self._store.fetch_one(
            """
            SELECT registry_entries.*, publications.published_at
            FROM registry_entries
            JOIN publications ON publications.publication_id = registry_entries.publication_id
            WHERE publications.state = 'PUBLISHED' AND publications.publication_id = ?
            """,
            (publication_id,),
        )
        return self._published_strategy_from_row(row) if row is not None else None

    def get_published_artifact(self, publication_id: str, role: str) -> PublicationArtifact | None:
        if role not in {self._REPORT_JSON_ROLE, self._REPORT_HTML_ROLE}:
            raise ValueError("only report artifact roles may be read through the public view")
        row = self._store.fetch_one(
            """
            SELECT artifacts.cas_uri, artifacts.content_hash, artifacts.byte_size, artifacts.media_type
            FROM artifacts
            JOIN artifact_links ON artifact_links.artifact_id = artifacts.artifact_id
            JOIN publications ON publications.publication_id = artifact_links.owner_id
            WHERE publications.publication_id = ? AND publications.state = 'PUBLISHED'
                AND artifact_links.owner_type = 'PUBLICATION' AND artifact_links.role = ?
            """,
            (publication_id, role),
        )
        if row is None:
            return None
        return PublicationArtifact(
            cas_uri=row["cas_uri"],
            content_hash=row["content_hash"],
            byte_size=row["byte_size"],
            media_type=row["media_type"],
        )

    def list_rejections(self) -> tuple[PublicationRejection, ...]:
        rows = self._store.fetch_all(
            "SELECT * FROM publication_rejections ORDER BY created_at ASC, publication_rejection_id ASC"
        )
        return tuple(self._rejection_from_row(row) for row in rows)

    def get_rejection(self, publication_rejection_id: str) -> PublicationRejection | None:
        row = self._store.fetch_one(
            "SELECT * FROM publication_rejections WHERE publication_rejection_id = ?",
            (publication_rejection_id,),
        )
        return self._rejection_from_row(row) if row is not None else None

    @staticmethod
    def _prerequisites_are_current(
        connection: sqlite3.Connection, publication: Publication
    ) -> bool:
        row = connection.execute(
            """
            SELECT publications.profile_hash AS publication_profile_hash,
                   validations.strategy_id, validations.candidate_hash, validations.lineage_id,
                   validations.profile_hash AS validation_profile_hash,
                   validations.pre_validation_decision, lineages.status AS lineage_status,
                   pbo_certificates.profile_hash AS pbo_profile_hash,
                   pbo_certificates.decision AS pbo_decision,
                   holdout_slots.profile_hash AS holdout_profile_hash,
                   holdout_slots.state AS holdout_state,
                   holdout_slots.consuming_validation_id AS consuming_validation_id
            FROM publications
            JOIN validations ON validations.validation_id = publications.validation_id
            JOIN lineages ON lineages.lineage_id = validations.lineage_id
            JOIN pbo_certificates
                ON pbo_certificates.pbo_certificate_id = validations.pbo_certificate_id
            JOIN holdout_slots ON holdout_slots.lineage_id = validations.lineage_id
            WHERE publications.publication_id = ? AND validations.validation_id = ?
            """,
            (publication.publication_id, publication.validation_id),
        ).fetchone()
        return bool(
            row is not None
            and row["strategy_id"] == publication.strategy_id
            and row["candidate_hash"] == publication.candidate_hash
            and row["lineage_id"] == publication.lineage_id
            and all(
                isinstance(row[profile], str) and bool(row[profile])
                for profile in (
                    "publication_profile_hash",
                    "validation_profile_hash",
                    "pbo_profile_hash",
                    "holdout_profile_hash",
                )
            )
            and row["publication_profile_hash"] == row["validation_profile_hash"]
            and row["publication_profile_hash"] == row["pbo_profile_hash"]
            and row["publication_profile_hash"] == row["holdout_profile_hash"]
            and publication.profile_hash is not None
            and row["publication_profile_hash"] == publication.profile_hash
            and row["pre_validation_decision"] == Decision.PASS.value
            and row["lineage_status"] == "CLOSED"
            and row["pbo_decision"] == Decision.PASS.value
            and row["holdout_state"] == "CONSUMED"
            and row["consuming_validation_id"] == publication.validation_id
        )

    def _ensure_artifact(
        self,
        connection: sqlite3.Connection,
        *,
        artifact: PublicationArtifact,
        created_at: str,
        created_by: str,
    ) -> str:
        row = connection.execute(
            "SELECT * FROM artifacts WHERE content_hash = ?", (artifact.content_hash,)
        ).fetchone()
        if row is not None:
            if (
                row["cas_uri"] != artifact.cas_uri
                or row["byte_size"] != artifact.byte_size
                or row["media_type"] != artifact.media_type
            ):
                raise PublicationStateError(
                    "existing artifact metadata conflicts with staged report"
                )
            return str(row["artifact_id"])
        artifact_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO artifacts (
                artifact_id, cas_uri, content_hash, byte_size, media_type, created_at, created_by,
                schema_version, code_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                artifact.cas_uri,
                artifact.content_hash,
                artifact.byte_size,
                artifact.media_type,
                created_at,
                created_by,
                self._SCHEMA_VERSION,
                self._CODE_VERSION,
            ),
        )
        return artifact_id

    @staticmethod
    def _insert_artifact_link(
        connection: sqlite3.Connection,
        *,
        artifact_id: str,
        publication_id: str,
        role: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO artifact_links (
                artifact_link_id, artifact_id, owner_type, owner_id, role, created_at
            ) VALUES (?, ?, 'PUBLICATION', ?, ?, ?)
            """,
            (str(uuid4()), artifact_id, publication_id, role, created_at),
        )

    @staticmethod
    def _insert_owner_event(
        connection: sqlite3.Connection,
        *,
        publication_id: str,
        event_kind: PublicationOwnerEventKind,
        owner_job_id: str | None,
        owner_epoch: int | None,
        orphan_hashes: tuple[str, ...],
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO publication_owner_events (
                event_id, publication_id, event_kind, owner_job_id, owner_epoch,
                orphan_hashes_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                publication_id,
                event_kind.value,
                owner_job_id,
                owner_epoch,
                SQLitePublicationRepository._hashes_json(orphan_hashes),
                created_at,
            ),
        )

    @staticmethod
    def _require_matching_identity(publication: Publication, command: PublicationCommand) -> None:
        if (
            publication.publication_id != command.publication_id
            or publication.validation_id != command.validation_id
            or publication.strategy_id != command.strategy_id
            or publication.candidate_hash != command.candidate_hash
            or publication.lineage_id != command.lineage_id
        ):
            raise PublicationStateError(
                "publication identity conflicts with its validation hand-off"
            )

    @staticmethod
    def _hashes_json(hashes: tuple[str, ...]) -> str | None:
        if not hashes:
            return None
        if len(hashes) != len(set(hashes)) or any(
            not _is_display_digest(value) for value in hashes
        ):
            raise ValueError("orphan hashes must be unique SHA-256 display digests")
        return json.dumps(list(hashes), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return format_utc_timestamp(value)

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        return parse_utc_timestamp(value)

    @classmethod
    def _publication_from_row(cls, row: sqlite3.Row) -> Publication:
        source = row
        orphan_json = source["prepared_orphan_hashes_json"]
        orphan_hashes = tuple(json.loads(orphan_json)) if orphan_json is not None else ()
        return Publication(
            publication_id=source["publication_id"],
            validation_id=source["validation_id"],
            strategy_id=source["strategy_id"],
            candidate_hash=source["candidate_hash"],
            lineage_id=source["lineage_id"],
            state=PublicationState(source["state"]),
            owner_token=source["owner_token"],
            owner_job_id=source["owner_job_id"],
            owner_epoch=source["owner_epoch"],
            owner_expires_at=(
                cls._parse_timestamp(source["owner_expires_at"])
                if source["owner_expires_at"] is not None
                else None
            ),
            attempt_count=source["attempt_count"],
            row_version=source["row_version"],
            prepared_orphan_hashes=orphan_hashes,
            failure_kind=(
                PublicationFailureKind(source["failure_kind"])
                if source["failure_kind"] is not None
                else None
            ),
            created_at=cls._parse_timestamp(source["created_at"]),
            updated_at=cls._parse_timestamp(source["updated_at"]),
            published_at=(
                cls._parse_timestamp(source["published_at"])
                if source["published_at"] is not None
                else None
            ),
            profile_hash=source["profile_hash"],
        )

    @classmethod
    def _rejection_from_row(cls, row: sqlite3.Row) -> PublicationRejection:
        source = row
        validation_id = source["validation_id"]
        if not isinstance(validation_id, str):
            raise PublicationStateError("publication rejection is missing a validation identity")
        return PublicationRejection(
            publication_rejection_id=source["publication_rejection_id"],
            validation_id=validation_id,
            input_hash=source["input_hash"],
            reason=PublicationRejectionReason(source["reason_code"]),
            created_at=cls._parse_timestamp(source["created_at"]),
        )

    @classmethod
    def _published_strategy_from_row(cls, row: sqlite3.Row) -> PublishedStrategy:
        source = row
        return PublishedStrategy(
            registry_entry=RegistryEntry(
                registry_entry_id=source["registry_entry_id"],
                publication_id=source["publication_id"],
                validation_id=source["validation_id"],
                strategy_id=source["strategy_id"],
                candidate_hash=source["candidate_hash"],
                lineage_id=source["lineage_id"],
                created_at=cls._parse_timestamp(source["created_at"]),
            ),
            published_at=cls._parse_timestamp(source["published_at"]),
        )


def publication_input_hash(
    *,
    validation_id: str,
    strategy_id: str,
    candidate_hash: str,
    lineage_id: str,
    profile_hash: str,
    disclosure_policy_hash: str,
    validation_decision: Decision,
    validation_complete: bool,
    lineage_closed: bool,
    holdout_complete: bool,
    holdout_decision: Decision,
    report: ResearchReport,
) -> str:
    """Compute the validation-to-publication hand-off identity from canonical fields."""
    return digest(
        "publication-input",
        {
            "candidate_hash": candidate_hash,
            "disclosure_policy_hash": disclosure_policy_hash,
            "holdout_complete": holdout_complete,
            "holdout_decision": holdout_decision.value,
            "lineage_closed": lineage_closed,
            "lineage_id": lineage_id,
            "profile_hash": profile_hash,
            "report_json": canonical_research_report_json(report).decode("utf-8"),
            "strategy_id": strategy_id,
            "validation_complete": validation_complete,
            "validation_decision": validation_decision.value,
            "validation_id": validation_id,
        },
    )


def _is_display_digest(value: str) -> bool:
    return (
        len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _staged_hashes(artifact: object) -> tuple[str, ...]:
    return (artifact.content_hash,) if isinstance(artifact, PublicationArtifact) else ()


__all__ = [
    "PublicationArtifactStorePort",
    "PublicationCommand",
    "PublicationRepositoryPort",
    "PublicationResult",
    "PublicationService",
    "PublicationStorageError",
    "SQLitePublicationRepository",
    "publication_input_hash",
]
