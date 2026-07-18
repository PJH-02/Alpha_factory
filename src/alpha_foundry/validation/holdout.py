"""One-shot sealed-holdout service with a mandatory pre-access atomic boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail, ErrorField
from alpha_foundry.domain.models import Decision, DisclosedMetric

from .pbo import PboCompletenessCertificate


class HoldoutPreAccessRejected(DomainError):
    """Typed proof that the consume transaction rejected before any commit."""


@dataclass(frozen=True, slots=True)
class HoldoutRequest:
    """The lineage-bound preconditions checked inside one consume transaction."""

    lineage_id: str
    validation_id: str
    profile_hash: str
    validation_decision: Decision
    pbo_certificate: PboCompletenessCertificate
    operation_id: str
    allowed_disclosure_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        fields = tuple(self.allowed_disclosure_fields)
        object.__setattr__(self, "allowed_disclosure_fields", fields)
        _require_identifier(self.lineage_id, "lineage_id")
        _require_identifier(self.validation_id, "validation_id")
        _require_identifier(self.profile_hash, "profile_hash")
        _require_identifier(self.operation_id, "operation_id")
        _require_unique(self.allowed_disclosure_fields, "allowed disclosure fields")
        for field in self.allowed_disclosure_fields:
            _require_identifier(field, "allowed disclosure field")

    @property
    def certificate_hash(self) -> str:
        """Return the only certificate identity that may authorize holdout."""
        return self.pbo_certificate.certificate_hash


@dataclass(frozen=True, slots=True)
class HoldoutAccessGrant:
    """One-use opaque capability issued only after consume-and-close commits."""

    lineage_id: str
    validation_id: str
    profile_hash: str
    certificate_hash: str
    operation_id: str
    grant_id: str
    capability: bytes
    consumed_at: datetime

    def __post_init__(self) -> None:
        _require_identifier(self.lineage_id, "lineage_id")
        _require_identifier(self.validation_id, "validation_id")
        _require_identifier(self.profile_hash, "profile_hash")
        _require_identifier(self.certificate_hash, "certificate_hash")
        _require_identifier(self.operation_id, "operation_id")
        _require_identifier(self.grant_id, "grant_id")
        if not isinstance(self.capability, bytes) or not self.capability:
            raise ValueError("capability must be a non-empty opaque byte token")
        if self.consumed_at.tzinfo is None or self.consumed_at.utcoffset() is None:
            raise ValueError("consumed_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class HoldoutDisclosure:
    """A post-access aggregate-only disclosure, with no sealed selector or trace."""

    metrics: tuple[DisclosedMetric, ...]

    def __post_init__(self) -> None:
        metrics = tuple(self.metrics)
        if any(type(metric) is not DisclosedMetric for metric in metrics):
            raise ValueError("holdout disclosure metrics must be typed DisclosedMetric values")
        object.__setattr__(self, "metrics", metrics)
        names = tuple(metric.name for metric in metrics)
        _require_unique(names, "disclosed metric names")


class HoldoutStatus(StrEnum):
    """Observable one-shot holdout outcomes."""

    BLOCKED_PREACCESS = "BLOCKED_PREACCESS"
    DISCLOSED = "DISCLOSED"
    FAILED_AFTER_CONSUMPTION = "FAILED_AFTER_CONSUMPTION"
    INDETERMINATE_AFTER_CONSUMPTION = "INDETERMINATE_AFTER_CONSUMPTION"


@dataclass(frozen=True, slots=True)
class HoldoutOutcome:
    """Data-only result that preserves whether sealed access became possible."""

    status: HoldoutStatus
    lineage_id: str
    validation_id: str
    access_granted: bool
    disclosure: HoldoutDisclosure | None
    reason: ErrorDetail | None

    def __post_init__(self) -> None:
        _require_identifier(self.lineage_id, "lineage_id")
        _require_identifier(self.validation_id, "validation_id")
        if self.status is HoldoutStatus.DISCLOSED:
            if not self.access_granted or self.disclosure is None or self.reason is not None:
                raise ValueError(
                    "a disclosed holdout outcome needs access and disclosure without a reason"
                )
        elif self.status is HoldoutStatus.BLOCKED_PREACCESS:
            if self.access_granted or self.disclosure is not None or self.reason is None:
                raise ValueError("a pre-access block needs no access/disclosure and a typed reason")
        elif self.status is HoldoutStatus.FAILED_AFTER_CONSUMPTION:
            if not self.access_granted or self.disclosure is not None or self.reason is None:
                raise ValueError(
                    "a consumed holdout failure needs access, no disclosure, and a typed reason"
                )
        elif self.status is HoldoutStatus.INDETERMINATE_AFTER_CONSUMPTION:
            if not self.access_granted or self.disclosure is not None or self.reason is None:
                raise ValueError(
                    "an indeterminate holdout outcome needs irreversible access state and a reason"
                )

    @property
    def blocks_publication(self) -> bool:
        """Return whether the outcome is ineligible for publication."""
        return self.status is not HoldoutStatus.DISCLOSED


class AtomicHoldoutRepository(Protocol):
    """Persistence port for one committed ``BEGIN IMMEDIATE`` consume-and-close operation."""

    def consume_slot_and_close_lineage(self, *, request: HoldoutRequest) -> HoldoutAccessGrant:
        """Atomically verify preconditions, consume the unused slot, close lineage, and commit.

        Only a :class:`HoldoutPreAccessRejected` proves that no commit occurred.
        Any other exception has an indeterminate outcome and must not enable retry.
        On success, return a lineage/validation/certificate/operation-bound,
        one-use capability only after the transaction commits.
        """


class SealedHoldoutReader(Protocol):
    """Reader callable only with a committed opaque access grant."""

    def read_sealed_holdout(self, *, access_grant: HoldoutAccessGrant) -> HoldoutDisclosure:
        """Verify and consume the opaque capability exactly once before sealed access."""


@dataclass(slots=True)
class HoldoutService:
    """Consumes and closes once before any sealed-data call; it never retries or reopens."""

    repository: AtomicHoldoutRepository
    sealed_reader: SealedHoldoutReader

    def run(self, *, request: HoldoutRequest) -> HoldoutOutcome:
        """Execute one consume boundary followed by at most one one-use sealed read."""
        preaccess_reason = _preaccess_block_reason(request)
        if preaccess_reason is not None:
            return HoldoutOutcome(
                status=HoldoutStatus.BLOCKED_PREACCESS,
                lineage_id=request.lineage_id,
                validation_id=request.validation_id,
                access_granted=False,
                disclosure=None,
                reason=preaccess_reason,
            )

        try:
            access_grant = self.repository.consume_slot_and_close_lineage(request=request)
        except HoldoutPreAccessRejected as error:
            return HoldoutOutcome(
                status=HoldoutStatus.BLOCKED_PREACCESS,
                lineage_id=request.lineage_id,
                validation_id=request.validation_id,
                access_granted=False,
                disclosure=None,
                reason=error.detail,
            )
        except Exception:
            return _indeterminate_outcome(
                request,
                "holdout consume-and-close transaction outcome is indeterminate",
            )

        grant_reason = _grant_error(request, access_grant)
        if grant_reason is not None:
            return _indeterminate_outcome(request, grant_reason.message)

        try:
            disclosure = self.sealed_reader.read_sealed_holdout(access_grant=access_grant)
        except Exception:
            return _indeterminate_outcome(
                request, "sealed holdout reader outcome is indeterminate after slot consumption"
            )

        disclosure_reason = _disclosure_error(request, disclosure)
        if disclosure_reason is not None:
            return HoldoutOutcome(
                status=HoldoutStatus.FAILED_AFTER_CONSUMPTION,
                lineage_id=request.lineage_id,
                validation_id=request.validation_id,
                access_granted=True,
                disclosure=None,
                reason=disclosure_reason,
            )
        return HoldoutOutcome(
            status=HoldoutStatus.DISCLOSED,
            lineage_id=request.lineage_id,
            validation_id=request.validation_id,
            access_granted=True,
            disclosure=disclosure,
            reason=None,
        )


def _indeterminate_outcome(request: HoldoutRequest, message: str) -> HoldoutOutcome:
    return HoldoutOutcome(
        status=HoldoutStatus.INDETERMINATE_AFTER_CONSUMPTION,
        lineage_id=request.lineage_id,
        validation_id=request.validation_id,
        access_granted=True,
        disclosure=None,
        reason=_holdout_reason(message),
    )


def _preaccess_block_reason(request: HoldoutRequest) -> ErrorDetail | None:
    if request.validation_decision is not Decision.PASS:
        return _validation_reason("a failed validation decision cannot access sealed holdout")
    certificate = request.pbo_certificate
    if certificate.profile_hash != request.profile_hash:
        return _validation_reason("PBO certificate profile hash does not match the holdout request")
    if not certificate.is_complete:
        return certificate.reason or ErrorDetail.for_code(
            ErrorCode.PBO_INCOMPLETE, "PBO certificate is incomplete"
        )
    if certificate.lineage_id != request.lineage_id:
        return _validation_reason("PBO certificate lineage does not match the holdout request")
    if certificate.validation_id != request.validation_id:
        return _validation_reason("PBO certificate validation does not match the holdout request")
    return None


def _grant_error(request: HoldoutRequest, grant: object) -> ErrorDetail | None:
    if not isinstance(grant, HoldoutAccessGrant):
        return _holdout_reason("repository returned an invalid holdout access grant")
    if grant.lineage_id != request.lineage_id:
        return _holdout_reason("holdout access grant lineage does not match the request")
    if grant.validation_id != request.validation_id:
        return _holdout_reason("holdout access grant validation does not match the request")
    if grant.profile_hash != request.profile_hash:
        return _holdout_reason("holdout access grant profile does not match the request")
    if grant.certificate_hash != request.certificate_hash:
        return _holdout_reason("holdout access grant certificate does not match the request")
    if grant.operation_id != request.operation_id:
        return _holdout_reason("holdout access grant operation does not match the request")
    return None


def _disclosure_error(request: HoldoutRequest, disclosure: object) -> ErrorDetail | None:
    if type(disclosure) is not HoldoutDisclosure:
        return _holdout_reason("sealed holdout reader returned an invalid disclosure")
    if any(type(metric) is not DisclosedMetric for metric in disclosure.metrics):
        return _holdout_reason("sealed holdout reader returned invalid disclosure metrics")
    actual_fields = tuple(metric.name for metric in disclosure.metrics)
    undeclared_field = next(
        (field for field in actual_fields if field not in request.allowed_disclosure_fields),
        None,
    )
    if undeclared_field is not None:
        return _validation_reason(
            "holdout disclosure does not exactly match the frozen allowlist",
            path=f"disclosure.{undeclared_field}",
        )
    if actual_fields != request.allowed_disclosure_fields:
        return _validation_reason(
            "holdout disclosure does not exactly match the frozen allowlist",
            path="disclosure",
        )
    return None


def _validation_reason(message: str, *, path: str = "validation") -> ErrorDetail:
    return ErrorDetail.for_code(
        ErrorCode.VALIDATION,
        message,
        details=(ErrorField(path=path, reason=message),),
    )


def _holdout_reason(message: str) -> ErrorDetail:
    return ErrorDetail.for_code(ErrorCode.HOLDOUT, message)


def _require_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")


def _require_unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
