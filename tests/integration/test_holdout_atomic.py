from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail
from alpha_foundry.domain.models import Decision, DisclosedMetric, RankingDirection
from alpha_foundry.validation.holdout import (
    HoldoutAccessGrant,
    HoldoutDisclosure,
    HoldoutOutcome,
    HoldoutPreAccessRejected,
    HoldoutRequest,
    HoldoutService,
    HoldoutStatus,
)
from alpha_foundry.validation.pbo import (
    FoldCoverage,
    PboCertificateDecision,
    PboCompletenessCertificate,
)

_PROFILE_HASH = "sha256:" + "a" * 64


def _request() -> HoldoutRequest:
    return HoldoutRequest(
        lineage_id="lineage-1",
        validation_id="validation-1",
        profile_hash=_PROFILE_HASH,
        validation_decision=Decision.PASS,
        pbo_certificate=PboCompletenessCertificate(
            lineage_id="lineage-1",
            validation_id="validation-1",
            profile_hash=_PROFILE_HASH,
            search_run_hash="sha256:" + "b" * 64,
            score_direction=RankingDirection.DESC,
            matrix_digest="sha256:" + "c" * 64,
            decision=PboCertificateDecision.PASS,
            reason=None,
            started_trial_ids=("trial-1",),
            terminal_trial_ids=("trial-1",),
            eligible_trial_ids=("trial-1",),
            matrix_trial_ids=("trial-1",),
            expected_fold_ids=("fold-1", "fold-2"),
            fold_coverage=(FoldCoverage("trial-1", ("fold-1", "fold-2")),),
            normal_stop=True,
            minimum_eligible=1,
        ),
        operation_id="holdout-operation-1",
        allowed_disclosure_fields=("sharpe",),
    )


@dataclass
class AtomicRepositoryFake:
    calls: list[str] = field(default_factory=list)
    slot_consumed: bool = False
    lineage_closed: bool = False

    def consume_slot_and_close_lineage(self, *, request: HoldoutRequest) -> HoldoutAccessGrant:
        self.calls.append("consume-and-close")
        if self.slot_consumed or self.lineage_closed:
            raise HoldoutPreAccessRejected(
                ErrorDetail.for_code(ErrorCode.HOLDOUT, "holdout slot is already consumed")
            )

        self.slot_consumed = True
        self.lineage_closed = True
        return HoldoutAccessGrant(
            lineage_id=request.lineage_id,
            validation_id=request.validation_id,
            profile_hash=request.profile_hash,
            certificate_hash=request.certificate_hash,
            operation_id=request.operation_id,
            grant_id="grant-1",
            capability=b"opaque-capability",
            consumed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


@dataclass
class OrderingReaderFake:
    repository: AtomicRepositoryFake
    calls: list[str]

    def read_sealed_holdout(self, *, access_grant: HoldoutAccessGrant) -> HoldoutDisclosure:
        assert self.repository.calls == ["consume-and-close"]
        assert self.repository.slot_consumed
        assert self.repository.lineage_closed
        assert access_grant.capability
        self.calls.append("sealed-read")
        return HoldoutDisclosure(
            metrics=(
                DisclosedMetric(
                    name="sharpe",
                    value=Decimal("1.25"),
                    threshold=Decimal("1"),
                    decision=Decision.PASS,
                ),
            )
        )


@dataclass
class StaticGrantRepositoryFake:
    grant: HoldoutAccessGrant
    calls: list[str] = field(default_factory=list)

    def consume_slot_and_close_lineage(self, *, request: HoldoutRequest) -> HoldoutAccessGrant:
        self.calls.append("consume-and-close")
        return self.grant


@dataclass
class FailingRepositoryFake:
    failure: Exception
    calls: list[str] = field(default_factory=list)

    def consume_slot_and_close_lineage(self, *, request: HoldoutRequest) -> HoldoutAccessGrant:
        self.calls.append("consume-and-close")
        raise self.failure


@dataclass
class ControlledReaderFake:
    response: HoldoutDisclosure | Exception
    calls: list[str] = field(default_factory=list)

    def read_sealed_holdout(self, *, access_grant: HoldoutAccessGrant) -> HoldoutDisclosure:
        self.calls.append("sealed-read")
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _grant(request: HoldoutRequest) -> HoldoutAccessGrant:
    return HoldoutAccessGrant(
        lineage_id=request.lineage_id,
        validation_id=request.validation_id,
        profile_hash=request.profile_hash,
        certificate_hash=request.certificate_hash,
        operation_id=request.operation_id,
        grant_id="grant-1",
        capability=b"opaque-capability",
        consumed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _disclosure(name: str = "sharpe") -> HoldoutDisclosure:
    return HoldoutDisclosure(
        metrics=(
            DisclosedMetric(
                name=name,
                value=Decimal("1.25"),
                threshold=Decimal("1"),
                decision=Decision.PASS,
            ),
        )
    )


def test_it_hold_001_consumes_slot_and_closes_lineage_before_any_sealed_read() -> None:
    repository = AtomicRepositoryFake()
    reader = OrderingReaderFake(repository=repository, calls=repository.calls)
    service = HoldoutService(repository=repository, sealed_reader=reader)

    outcome = service.run(request=_request())

    assert outcome.status is HoldoutStatus.DISCLOSED
    assert outcome.access_granted
    assert outcome.reason is None
    assert outcome.disclosure is not None
    assert tuple(metric.name for metric in outcome.disclosure.metrics) == ("sharpe",)
    assert repository.calls == ["consume-and-close", "sealed-read"]
    assert repository.slot_consumed
    assert repository.lineage_closed


def test_it_hold_002_typed_preaccess_rejection_blocks_a_second_sealed_read() -> None:
    repository = AtomicRepositoryFake()
    reader = OrderingReaderFake(repository=repository, calls=repository.calls)
    service = HoldoutService(repository=repository, sealed_reader=reader)
    request = _request()

    first = service.run(request=request)
    second = service.run(request=request)

    assert first.status is HoldoutStatus.DISCLOSED
    assert second.status is HoldoutStatus.BLOCKED_PREACCESS
    assert not second.access_granted
    assert second.reason is not None
    assert second.reason.code is ErrorCode.HOLDOUT
    assert repository.calls == ["consume-and-close", "sealed-read", "consume-and-close"]
    assert second.blocks_publication


@pytest.mark.parametrize(
    ("grant_update", "reason_fragment"),
    [
        ({"lineage_id": "other-lineage"}, "lineage does not match"),
        ({"validation_id": "other-validation"}, "validation does not match"),
        ({"profile_hash": "other-profile"}, "profile does not match"),
        ({"certificate_hash": "sha256:" + "d" * 64}, "certificate does not match"),
        ({"operation_id": "other-operation"}, "operation does not match"),
    ],
)
def test_it_hold_003_invalid_returned_grants_are_indeterminate_without_a_sealed_read(
    grant_update: dict[str, str], reason_fragment: str
) -> None:
    request = _request()
    repository = StaticGrantRepositoryFake(grant=replace(_grant(request), **grant_update))
    reader = ControlledReaderFake(response=_disclosure())
    outcome = HoldoutService(repository=repository, sealed_reader=reader).run(request=request)

    assert outcome.status is HoldoutStatus.INDETERMINATE_AFTER_CONSUMPTION
    assert outcome.access_granted
    assert outcome.disclosure is None
    assert outcome.reason is not None
    assert outcome.reason.code is ErrorCode.HOLDOUT
    assert reason_fragment in outcome.reason.message
    assert repository.calls == ["consume-and-close"]
    assert reader.calls == []


@pytest.mark.parametrize(
    ("blocked_request", "expected_code"),
    [
        (replace(_request(), validation_decision=Decision.FAIL), ErrorCode.VALIDATION),
        (
            replace(
                _request(),
                pbo_certificate=replace(
                    _request().pbo_certificate,
                    profile_hash="sha256:" + "d" * 64,
                ),
            ),
            ErrorCode.VALIDATION,
        ),
        (
            replace(
                _request(),
                pbo_certificate=replace(
                    _request().pbo_certificate,
                    decision=PboCertificateDecision.FAIL,
                    reason=ErrorDetail.for_code(
                        ErrorCode.PBO_INCOMPLETE, "PBO ledger is incomplete"
                    ),
                ),
            ),
            ErrorCode.PBO_INCOMPLETE,
        ),
    ],
)
def test_it_hold_004_preaccess_validation_failures_never_consume_or_read_sealed_data(
    blocked_request: HoldoutRequest, expected_code: ErrorCode
) -> None:
    repository = AtomicRepositoryFake()
    reader = OrderingReaderFake(repository=repository, calls=repository.calls)

    outcome = HoldoutService(repository=repository, sealed_reader=reader).run(
        request=blocked_request
    )

    assert outcome.status is HoldoutStatus.BLOCKED_PREACCESS
    assert not outcome.access_granted
    assert outcome.disclosure is None
    assert outcome.reason is not None
    assert outcome.reason.code is expected_code
    assert outcome.blocks_publication
    assert repository.calls == []


@pytest.mark.parametrize(
    "failure",
    [
        DomainError(
            ErrorDetail.for_code(ErrorCode.HOLDOUT, "consume-and-close transaction failed")
        ),
        RuntimeError("consume-and-close transaction crashed"),
    ],
)
def test_it_hold_005_transaction_failure_is_indeterminate_without_a_sealed_read(
    failure: Exception,
) -> None:
    repository = FailingRepositoryFake(failure=failure)
    reader = ControlledReaderFake(response=_disclosure())

    outcome = HoldoutService(repository=repository, sealed_reader=reader).run(request=_request())

    assert outcome.status is HoldoutStatus.INDETERMINATE_AFTER_CONSUMPTION
    assert outcome.access_granted
    assert outcome.disclosure is None
    assert outcome.reason is not None
    assert outcome.reason.code is ErrorCode.HOLDOUT
    assert outcome.blocks_publication
    assert repository.calls == ["consume-and-close"]
    assert reader.calls == []


@pytest.mark.parametrize(
    "failure",
    [
        DomainError(ErrorDetail.for_code(ErrorCode.HOLDOUT, "sealed reader denied access")),
        RuntimeError("sealed reader crashed"),
    ],
)
def test_it_hold_006_reader_failure_is_indeterminate_and_retry_cannot_repeat_sealed_read(
    failure: Exception,
) -> None:
    repository = AtomicRepositoryFake()
    reader = ControlledReaderFake(response=failure, calls=repository.calls)
    service = HoldoutService(repository=repository, sealed_reader=reader)

    first = service.run(request=_request())
    retry = service.run(request=_request())

    assert first.status is HoldoutStatus.INDETERMINATE_AFTER_CONSUMPTION
    assert first.access_granted
    assert first.disclosure is None
    assert first.reason is not None
    assert first.reason.code is ErrorCode.HOLDOUT
    assert first.blocks_publication
    assert repository.slot_consumed
    assert repository.lineage_closed
    assert retry.status is HoldoutStatus.BLOCKED_PREACCESS
    assert not retry.access_granted
    assert repository.calls == ["consume-and-close", "sealed-read", "consume-and-close"]


def test_it_hold_007_undeclared_reader_disclosure_is_dropped_after_consumption() -> None:
    repository = AtomicRepositoryFake()
    reader = ControlledReaderFake(response=_disclosure("raw_returns"), calls=repository.calls)

    outcome = HoldoutService(repository=repository, sealed_reader=reader).run(request=_request())

    assert outcome.status is HoldoutStatus.FAILED_AFTER_CONSUMPTION
    assert outcome.access_granted
    assert outcome.disclosure is None
    assert outcome.reason is not None
    assert outcome.reason.code is ErrorCode.VALIDATION
    assert outcome.reason.details[0].path == "disclosure.raw_returns"
    assert repository.calls == ["consume-and-close", "sealed-read"]


def test_it_hold_008_rejects_naive_grants_and_invalid_outcome_state_combinations() -> None:
    request = _request()
    reason = ErrorDetail.for_code(ErrorCode.HOLDOUT, "blocked")

    with pytest.raises(ValueError, match="consumed_at must be timezone-aware"):
        HoldoutAccessGrant(
            lineage_id=request.lineage_id,
            validation_id=request.validation_id,
            profile_hash=request.profile_hash,
            certificate_hash=request.certificate_hash,
            operation_id=request.operation_id,
            grant_id="grant-1",
            capability=b"opaque-capability",
            consumed_at=datetime(2026, 1, 1),
        )
    with pytest.raises(ValueError, match="lineage_id must be a non-empty string"):
        replace(request, lineage_id="")
    with pytest.raises(ValueError, match="allowed disclosure fields must be unique"):
        replace(request, allowed_disclosure_fields=("sharpe", "sharpe"))
    with pytest.raises(ValueError, match="disclosed metric names must be unique"):
        HoldoutDisclosure(metrics=(_disclosure().metrics[0], _disclosure().metrics[0]))

    with pytest.raises(ValueError, match="needs access and disclosure"):
        HoldoutOutcome(
            status=HoldoutStatus.DISCLOSED,
            lineage_id=request.lineage_id,
            validation_id=request.validation_id,
            access_granted=False,
            disclosure=None,
            reason=None,
        )
    with pytest.raises(ValueError, match="pre-access block"):
        HoldoutOutcome(
            status=HoldoutStatus.BLOCKED_PREACCESS,
            lineage_id=request.lineage_id,
            validation_id=request.validation_id,
            access_granted=True,
            disclosure=None,
            reason=reason,
        )
    with pytest.raises(ValueError, match="consumed holdout failure"):
        HoldoutOutcome(
            status=HoldoutStatus.FAILED_AFTER_CONSUMPTION,
            lineage_id=request.lineage_id,
            validation_id=request.validation_id,
            access_granted=True,
            disclosure=_disclosure(),
            reason=reason,
        )
