from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alpha_foundry.application.publication import PublicationCommand, PublicationService
from alpha_foundry.domain.models import (
    Decision,
    DisclosedMetric,
    Domain,
    GateComparison,
    RankingDirection,
    RankingRule,
    ResearchReport,
    ValidationGate,
    ValidationProfile,
)
from alpha_foundry.publication import (
    Publication,
    PublicationArtifact,
    PublicationRejection,
    PublicationRejectionReason,
    PublishedStrategy,
)
from alpha_foundry.validation.disclosure import HoldoutDisclosure, HoldoutDisclosurePolicy

_NOW = datetime(2026, 7, 12, 9, 30, tzinfo=UTC)


def _digest(number: int) -> str:
    return f"sha256:{number:064x}"


def _command() -> PublicationCommand:
    metric = DisclosedMetric(
        name="sharpe",
        value=Decimal("1.25"),
        threshold=Decimal("1"),
        decision=Decision.PASS,
    )
    profile = ValidationProfile(
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
    report = ResearchReport(
        publication_id="publication-1",
        strategy_id="strategy-1",
        domain=Domain.FACTOR,
        evidence_ids=("validation-1",),
        provider_attempt_ids=("provider-attempt-1",),
        dataset_hashes=(_digest(4),),
        config_hash=_digest(5),
        code_hash=_digest(6),
        schema_hashes=(_digest(7),),
        lineage_id="lineage-1",
        validation_decision=Decision.PASS,
        disclosed_metrics=(metric,),
        artifact_hashes=(_digest(8),),
        created_at=_NOW,
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
        disclosure=HoldoutDisclosure(decision=Decision.PASS, disclosed_metrics=(metric,)),
        disclosure_policy=HoldoutDisclosurePolicy(
            policy_id="disclosure-policy-1",
            version="v1",
            policy_hash=profile.disclosure_policy_hash,
            allowed_disclosure_fields=("sharpe",),
        ),
        report=report,
        input_hash=_digest(10),
    )
    return replace(command, input_hash=command.expected_input_hash)


class NoArtifactStore:
    def __init__(self) -> None:
        self.calls = 0

    def put_bytes(self, payload: bytes, media_type: str) -> object:
        self.calls += 1
        raise AssertionError("rejected publication must not stage artifacts")

    def read_bytes(self, reference: str) -> bytes:
        raise AssertionError("rejected publication cannot expose report artifacts")


class RejectionRepositoryFake:
    def __init__(self, stored_reason: PublicationRejectionReason | None = None) -> None:
        self.stored_reason = stored_reason
        self.preflight_calls = 0
        self.publication_calls = 0
        self.rejections: dict[tuple[str, str], PublicationRejection] = {}

    def preflight_reason(self, command: PublicationCommand) -> PublicationRejectionReason | None:
        self.preflight_calls += 1
        return self.stored_reason

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
        self.publication_calls += 1
        raise AssertionError("rejected publication must not enter the lifecycle")

    def list_published(self) -> tuple[PublishedStrategy, ...]:
        return ()

    def get_published(self, publication_id: str) -> PublishedStrategy | None:
        return None

    def get_published_artifact(self, publication_id: str, role: str) -> PublicationArtifact | None:
        return None

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


Mutation = Callable[[PublicationCommand], PublicationCommand]


@pytest.mark.parametrize(
    ("mutate", "stored_reason", "expected_reason"),
    (
        (
            lambda command: replace(command, validation_complete=False),
            None,
            PublicationRejectionReason.VALIDATION_NOT_PASS,
        ),
        (
            lambda command: replace(command, lineage_closed=False),
            None,
            PublicationRejectionReason.LINEAGE_NOT_CLOSED,
        ),
        (
            lambda command: replace(
                command,
                disclosure=HoldoutDisclosure(
                    decision=Decision.PASS,
                    disclosed_metrics=(
                        DisclosedMetric(
                            name="raw_returns",
                            value=Decimal("0.01"),
                            decision=Decision.PASS,
                        ),
                    ),
                ),
            ),
            None,
            PublicationRejectionReason.DISCLOSURE_INVALID,
        ),
        (
            lambda command: replace(command, input_hash=_digest(99)),
            None,
            PublicationRejectionReason.INPUT_HASH_MISMATCH,
        ),
        (
            lambda command: command,
            PublicationRejectionReason.INPUT_HASH_MISMATCH,
            PublicationRejectionReason.INPUT_HASH_MISMATCH,
        ),
    ),
    ids=(
        "validation-not-pass",
        "open-lineage",
        "unknown-disclosure-field",
        "tampered-input-hash",
        "persisted-preflight-mismatch",
    ),
)
def test_it_pub_invalid_001_preflight_rejection_is_internal_idempotent_and_non_public(
    mutate: Mutation,
    stored_reason: PublicationRejectionReason | None,
    expected_reason: PublicationRejectionReason,
) -> None:
    repository = RejectionRepositoryFake(stored_reason)
    artifacts = NoArtifactStore()
    service = PublicationService(repository, artifacts)
    command = mutate(_command())

    first = service.publish(command)
    second = service.publish(command)

    assert first.is_rejected
    assert second.is_rejected
    assert first.publication is None
    assert first.rejection is not None
    assert first.rejection.reason is expected_reason
    assert second.rejection == first.rejection
    assert len(repository.rejections) == 1
    assert repository.publication_calls == 0
    assert artifacts.calls == 0
    assert service.list_published() == ()
    assert service.get_published(command.publication_id) is None
    assert service.report(command.publication_id) is None
    assert service.report_html(command.publication_id) is None
