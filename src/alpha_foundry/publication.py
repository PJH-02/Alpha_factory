"""Publication lifecycle value objects and visibility-safe records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class PublicationState(StrEnum):
    """The complete lifecycle for a validation-eligible publication."""

    AVAILABLE = "AVAILABLE"
    PREPARING = "PREPARING"
    PUBLISHED = "PUBLISHED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"


class PublicationFailureKind(StrEnum):
    """The only retryable failure classifications for a publication attempt."""

    ARTIFACT_IO = "ARTIFACT_IO"
    REPORT_RENDER = "REPORT_RENDER"
    STORAGE_COMMIT = "STORAGE_COMMIT"
    OWNER_INTERRUPTED = "OWNER_INTERRUPTED"


class PublicationRejectionReason(StrEnum):
    """Non-retryable preflight reasons retained only in the internal registry."""

    VALIDATION_NOT_PASS = "VALIDATION_NOT_PASS"
    LINEAGE_NOT_CLOSED = "LINEAGE_NOT_CLOSED"
    DISCLOSURE_INVALID = "DISCLOSURE_INVALID"
    INPUT_HASH_MISMATCH = "INPUT_HASH_MISMATCH"


class PublicationOwnerEventKind(StrEnum):
    """Append-only ownership audit events."""

    ACQUIRED = "ACQUIRED"
    RELEASED_STALE = "RELEASED_STALE"
    FAILED_ATTEMPT = "FAILED_ATTEMPT"
    PUBLISHED = "PUBLISHED"


class PublicationStateError(RuntimeError):
    """Raised when a repository cannot preserve publication ownership invariants."""


@dataclass(frozen=True, slots=True)
class Publication:
    """Mutable-state snapshot with immutable identity and explicit ownership."""

    publication_id: str
    validation_id: str
    strategy_id: str
    candidate_hash: str
    lineage_id: str
    state: PublicationState
    owner_token: str | None
    owner_job_id: str | None
    owner_epoch: int
    owner_expires_at: datetime | None
    attempt_count: int
    row_version: int
    prepared_orphan_hashes: tuple[str, ...] = ()
    failure_kind: PublicationFailureKind | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    published_at: datetime | None = None
    profile_hash: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(
            self.publication_id,
            self.validation_id,
            self.strategy_id,
            self.candidate_hash,
            self.lineage_id,
        )
        if not isinstance(self.state, PublicationState):
            raise ValueError("publication state must be a PublicationState")
        if self.failure_kind is not None and not isinstance(
            self.failure_kind, PublicationFailureKind
        ):
            raise ValueError("publication failure kind must be a PublicationFailureKind")
        if self.owner_epoch < 0:
            raise ValueError("owner_epoch must not be negative")
        if self.attempt_count < 0:
            raise ValueError("attempt_count must not be negative")
        if self.row_version < 1:
            raise ValueError("row_version must be at least one")
        _require_hashes(self.prepared_orphan_hashes)
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        _require_utc(self.published_at, "published_at")
        _require_utc(self.owner_expires_at, "owner_expires_at")
        if self.profile_hash is not None:
            _require_nonblank(self.profile_hash)

        has_owner = (
            self.owner_token is not None
            or self.owner_job_id is not None
            or self.owner_expires_at is not None
        )
        if self.state is PublicationState.PREPARING:
            if (
                not self.owner_token
                or not self.owner_job_id
                or self.owner_expires_at is None
                or self.owner_epoch < 1
            ):
                raise ValueError("preparing publication requires a complete owner lease")
            if self.published_at is not None:
                raise ValueError("preparing publication cannot be published")
            return

        if has_owner:
            raise ValueError("non-preparing publication must be ownerless")
        if self.state is PublicationState.PUBLISHED:
            if (
                self.published_at is None
                or self.failure_kind is not None
                or self.prepared_orphan_hashes
            ):
                raise ValueError("published publication requires clean terminal state")
            return
        if self.published_at is not None:
            raise ValueError("non-published publication cannot have a published timestamp")
        if self.state is PublicationState.AVAILABLE and (
            self.failure_kind is not None or self.prepared_orphan_hashes
        ):
            raise ValueError("available publication cannot retain failed-attempt state")
        if self.state is PublicationState.FAILED_RETRYABLE and self.failure_kind is None:
            raise ValueError("retryable publication failure requires a failure kind")


@dataclass(frozen=True, slots=True)
class PublicationArtifact:
    """Verified content-addressed artifact metadata, safe to link atomically."""

    cas_uri: str
    content_hash: str
    byte_size: int
    media_type: str

    def __post_init__(self) -> None:
        if not self.cas_uri:
            raise ValueError("artifact cas_uri must not be blank")
        if len(self.content_hash) != 71 or not self.content_hash.startswith("sha256:"):
            raise ValueError("artifact content_hash must be a SHA-256 display digest")
        digest = self.content_hash.removeprefix("sha256:")
        if any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("artifact content_hash must contain lowercase hexadecimal")
        if self.cas_uri != f"cas://sha256/{digest}":
            raise ValueError("artifact cas_uri must match its content hash")
        if self.byte_size < 0:
            raise ValueError("artifact byte_size must not be negative")
        if not self.media_type:
            raise ValueError("artifact media_type must not be blank")


@dataclass(frozen=True, slots=True)
class StagedPublicationArtifacts:
    """The two immutable report artifacts staged before the final database commit."""

    json_report: PublicationArtifact
    html_report: PublicationArtifact

    def __post_init__(self) -> None:
        if self.json_report.media_type != "application/json":
            raise ValueError("JSON report artifact must use application/json")
        if self.html_report.media_type != "text/html":
            raise ValueError("HTML report artifact must use text/html")
        _require_hashes(self.orphan_hashes)

    @property
    def orphan_hashes(self) -> tuple[str, str]:
        return (self.json_report.content_hash, self.html_report.content_hash)


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """One immutable public pass entry, created only with a PUBLISHED transition."""

    registry_entry_id: str
    publication_id: str
    validation_id: str
    strategy_id: str
    candidate_hash: str
    lineage_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require_nonblank(
            self.registry_entry_id,
            self.publication_id,
            self.validation_id,
            self.strategy_id,
            self.candidate_hash,
            self.lineage_id,
        )
        _require_utc(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class PublicationRejection:
    """Idempotent, non-public preflight evidence for ineligible work."""

    publication_rejection_id: str
    validation_id: str
    input_hash: str
    reason: PublicationRejectionReason
    created_at: datetime

    def __post_init__(self) -> None:
        _require_nonblank(self.publication_rejection_id, self.validation_id, self.input_hash)
        _require_utc(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class PublishedStrategy:
    """A view row that is safe for researcher-facing queries."""

    registry_entry: RegistryEntry
    published_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.published_at, "published_at")


def _require_nonblank(*values: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError("publication identifiers must not be blank")


def _require_hashes(hashes: tuple[str, ...]) -> None:
    if len(hashes) != len(set(hashes)):
        raise ValueError("prepared orphan hashes must be unique")
    for content_hash in hashes:
        if len(content_hash) != 71 or not content_hash.startswith("sha256:"):
            raise ValueError("prepared orphan hashes must be SHA-256 display digests")
        if any(character not in "0123456789abcdef" for character in content_hash[7:]):
            raise ValueError("prepared orphan hashes must contain lowercase hexadecimal")


def _require_utc(value: datetime | None, name: str) -> None:
    if value is None:
        return
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "Publication",
    "PublicationArtifact",
    "PublicationFailureKind",
    "PublicationOwnerEventKind",
    "PublicationRejection",
    "PublicationRejectionReason",
    "PublicationState",
    "PublicationStateError",
    "PublishedStrategy",
    "RegistryEntry",
    "StagedPublicationArtifacts",
]
