from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from alpha_foundry.domain.errors import ErrorCode, ErrorDetail
from alpha_foundry.domain.models import Decision, RankingDirection
from alpha_foundry.validation.holdout import (
    HoldoutAccessGrant,
    HoldoutDisclosure,
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
class DurableConsumeFake:
    calls: list[str] = field(default_factory=list)
    consumed: bool = False
    closed: bool = False

    def consume_slot_and_close_lineage(self, *, request: HoldoutRequest) -> HoldoutAccessGrant:
        self.calls.append("consume-and-close")
        if self.consumed or self.closed:
            raise HoldoutPreAccessRejected(
                ErrorDetail.for_code(ErrorCode.HOLDOUT, "lineage is permanently closed")
            )

        self.consumed = True
        self.closed = True
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
class CrashAfterReadStartFake:
    repository: DurableConsumeFake
    calls: list[str] = field(default_factory=list)

    def read_sealed_holdout(self, *, access_grant: HoldoutAccessGrant) -> HoldoutDisclosure:
        self.calls.append("sealed-read")
        assert self.repository.consumed
        assert self.repository.closed
        assert access_grant.capability
        raise RuntimeError("simulated process crash after sealed reader starts")


def test_it_hold_003_reader_crash_keeps_consumption_and_closure_permanent() -> None:
    repository = DurableConsumeFake()
    reader = CrashAfterReadStartFake(repository)
    service = HoldoutService(repository=repository, sealed_reader=reader)
    request = _request()

    crashed = service.run(request=request)
    retry = service.run(request=request)

    assert crashed.status is HoldoutStatus.INDETERMINATE_AFTER_CONSUMPTION
    assert crashed.access_granted
    assert crashed.disclosure is None
    assert crashed.reason is not None
    assert crashed.reason.code is ErrorCode.HOLDOUT
    assert not crashed.reason.retryable
    assert repository.consumed
    assert repository.closed
    assert reader.calls == ["sealed-read"]

    assert retry.status is HoldoutStatus.BLOCKED_PREACCESS
    assert not retry.access_granted
    assert retry.disclosure is None
    assert retry.reason is not None
    assert retry.reason.code is ErrorCode.HOLDOUT
    assert not retry.reason.retryable
    assert repository.calls == ["consume-and-close", "consume-and-close"]
    assert reader.calls == ["sealed-read"]
