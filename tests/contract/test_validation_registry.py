from decimal import Decimal

import pytest

from alpha_foundry.domain.errors import ErrorCode, ErrorDetail
from alpha_foundry.domain.models import (
    Decision,
    Domain,
    GateComparison,
    Metric,
    RankingDirection,
    RankingRule,
    ValidationGate,
    ValidationProfile,
)
from alpha_foundry.validation.pbo import (
    FoldCoverage,
    PboCertificateDecision,
    PboCompletenessCertificate,
)
from alpha_foundry.validation.registry import GateRegistry, GateScope, publication_block_reason

_HASH = "sha256:" + "a" * 64
_OTHER_HASH = "sha256:" + "b" * 64
_COMMON_GATES = (
    ValidationGate(
        gate_id="common-drawdown",
        metric_name="drawdown",
        comparison=GateComparison.GTE,
        threshold=Decimal("-0.10"),
    ),
)
_DOMAIN_GATES = (
    ValidationGate(
        gate_id="factor-sharpe",
        metric_name="sharpe",
        comparison=GateComparison.GTE,
        threshold=Decimal("1.00"),
    ),
)


def _profile(
    *,
    profile_hash: str = _HASH,
    domain: Domain = Domain.FACTOR,
    version: str = "1.0.0",
    hard_gates: tuple[ValidationGate, ...] = _COMMON_GATES + _DOMAIN_GATES,
) -> ValidationProfile:
    return ValidationProfile(
        validation_profile_id="factor-profile",
        domain=domain,
        version=version,
        profile_hash=profile_hash,
        hard_gates=hard_gates,
        ranking=(
            RankingRule(
                metric_name="sharpe",
                direction=RankingDirection.DESC,
                quantization=Decimal("0.01"),
            ),
        ),
        fold_ids=("fold-1", "fold-2"),
        pbo_minimum_eligible=1,
        patience=1,
        holdout_policy_hash=_HASH,
        disclosure_policy_hash=_HASH,
        allowed_disclosure_fields=("sharpe",),
    )


def _complete_certificate(*, profile_hash: str = _HASH) -> PboCompletenessCertificate:
    return PboCompletenessCertificate(
        lineage_id="lineage-1",
        validation_id="validation-1",
        profile_hash=profile_hash,
        search_run_hash=_OTHER_HASH,
        score_direction=RankingDirection.DESC,
        matrix_digest=_HASH,
        decision=PboCertificateDecision.PASS,
        reason=None,
        started_trial_ids=("trial-1",),
        terminal_trial_ids=("trial-1",),
        eligible_trial_ids=("trial-1",),
        matrix_trial_ids=("trial-1",),
        expected_fold_ids=("fold-1", "fold-2"),
        fold_coverage=(FoldCoverage(trial_id="trial-1", observed_fold_ids=("fold-1", "fold-2")),),
        normal_stop=True,
        minimum_eligible=1,
    )


def test_ct_val_001_freezes_and_evaluates_common_gates_before_domain_gates() -> None:
    profile = _profile()
    registry = GateRegistry.freeze(
        profile=profile,
        common_gates=_COMMON_GATES,
        domain_gates=_DOMAIN_GATES,
    )

    evaluation = registry.evaluate(
        profile=profile,
        metrics=(
            Metric(name="drawdown", value=Decimal("-0.05")),
            Metric(name="sharpe", value=Decimal("1.25")),
        ),
    )

    assert registry.gates == profile.hard_gates
    assert [(item.gate.gate_id, item.scope) for item in registry.registered_gates] == [
        ("common-drawdown", GateScope.COMMON),
        ("factor-sharpe", GateScope.DOMAIN),
    ]
    assert evaluation.decision is Decision.PASS
    assert [(check.gate_id, check.scope) for check in evaluation.checks] == [
        ("common-drawdown", GateScope.COMMON),
        ("factor-sharpe", GateScope.DOMAIN),
    ]


def test_ct_val_002_rejects_a_registry_sequence_that_differs_from_the_frozen_profile() -> None:
    profile = _profile()

    with pytest.raises(ValueError, match="exactly match the frozen profile"):
        GateRegistry.freeze(
            profile=profile,
            common_gates=_DOMAIN_GATES,
            domain_gates=_COMMON_GATES,
        )


def test_ct_val_003_stops_on_the_first_common_hard_failure_with_a_typed_reason() -> None:
    profile = _profile()
    registry = GateRegistry.freeze(
        profile=profile,
        common_gates=_COMMON_GATES,
        domain_gates=_DOMAIN_GATES,
    )

    evaluation = registry.evaluate(
        profile=profile,
        metrics=(Metric(name="sharpe", value=Decimal("1.25")),),
    )

    assert evaluation.decision is Decision.FAIL
    assert [(check.gate_id, check.scope) for check in evaluation.checks] == [
        ("common-drawdown", GateScope.COMMON),
    ]
    assert evaluation.reason is not None
    assert evaluation.reason.code is ErrorCode.VALIDATION
    assert not evaluation.reason.retryable
    assert evaluation.reason.details[0].path == "metrics.drawdown"


def test_ct_val_004_returns_a_typed_failure_for_a_profile_hash_mismatch() -> None:
    registry = GateRegistry.freeze(
        profile=_profile(),
        common_gates=_COMMON_GATES,
        domain_gates=_DOMAIN_GATES,
    )

    evaluation = registry.evaluate(profile=_profile(profile_hash=_OTHER_HASH), metrics=())

    assert evaluation.decision is Decision.FAIL
    assert evaluation.checks == ()
    assert evaluation.reason is not None
    assert evaluation.reason.code is ErrorCode.VALIDATION
    assert not evaluation.reason.retryable


def test_ct_val_005_rejects_duplicate_gate_ids_in_a_frozen_registry() -> None:
    with pytest.raises(ValueError, match="duplicate gate IDs"):
        GateRegistry(
            profile_hash=_HASH,
            domain=Domain.FACTOR,
            common_gates=(_COMMON_GATES[0], _COMMON_GATES[0]),
            domain_gates=(),
        )


@pytest.mark.parametrize(
    ("profile", "reason_fragment"),
    (
        (_profile(domain=Domain.STAT_ARB), "domain does not match"),
        (
            _profile(version="2.0.0", profile_hash=_OTHER_HASH),
            "hash does not match",
        ),
    ),
)
def test_ct_val_006_rejects_profiles_outside_the_frozen_domain_and_semantic_hash(
    profile: ValidationProfile, reason_fragment: str
) -> None:
    registry = GateRegistry.freeze(
        profile=_profile(),
        common_gates=_COMMON_GATES,
        domain_gates=_DOMAIN_GATES,
    )

    evaluation = registry.evaluate(profile=profile, metrics=())

    assert evaluation.decision is Decision.FAIL
    assert evaluation.reason is not None
    assert evaluation.reason.code is ErrorCode.VALIDATION
    assert reason_fragment in evaluation.reason.message


def test_ct_val_007_rejects_a_profile_with_changed_frozen_gate_semantics() -> None:
    changed_drawdown_gate = ValidationGate(
        gate_id="common-drawdown",
        metric_name="drawdown",
        comparison=GateComparison.GTE,
        threshold=Decimal("-0.15"),
    )
    registry = GateRegistry.freeze(
        profile=_profile(),
        common_gates=_COMMON_GATES,
        domain_gates=_DOMAIN_GATES,
    )

    evaluation = registry.evaluate(
        profile=_profile(hard_gates=(changed_drawdown_gate, *_DOMAIN_GATES)),
        metrics=(),
    )

    assert evaluation.decision is Decision.FAIL
    assert evaluation.reason is not None
    assert evaluation.reason.code is ErrorCode.VALIDATION
    assert "gates do not match" in evaluation.reason.message


def test_ct_val_008_rejects_duplicate_metric_values_before_gate_evaluation() -> None:
    profile = _profile()
    registry = GateRegistry.freeze(
        profile=profile,
        common_gates=_COMMON_GATES,
        domain_gates=_DOMAIN_GATES,
    )

    evaluation = registry.evaluate(
        profile=profile,
        metrics=(
            Metric(name="drawdown", value=Decimal("-0.05")),
            Metric(name="drawdown", value=Decimal("-0.50")),
            Metric(name="sharpe", value=Decimal("2.00")),
        ),
    )

    assert evaluation.decision is Decision.FAIL
    assert len(evaluation.checks) == 1
    assert evaluation.checks[0].observed_value is None
    assert evaluation.reason is not None
    assert evaluation.reason.details[0].path == "metrics.drawdown"
    assert "supplied more than once" in evaluation.reason.message


@pytest.mark.parametrize(
    ("comparison", "observed_value", "decision"),
    (
        (GateComparison.LT, Decimal("0.99"), Decision.PASS),
        (GateComparison.LTE, Decimal("1.00"), Decision.PASS),
        (GateComparison.GT, Decimal("1.01"), Decision.PASS),
        (GateComparison.GTE, Decimal("1.00"), Decision.PASS),
        (GateComparison.EQ, Decimal("1.00"), Decision.PASS),
        (GateComparison.LT, Decimal("1.00"), Decision.FAIL),
    ),
)
def test_ct_val_009_evaluates_comparison_boundaries(
    comparison: GateComparison, observed_value: Decimal, decision: Decision
) -> None:
    gate = ValidationGate(
        gate_id="boundary",
        metric_name="boundary",
        comparison=comparison,
        threshold=Decimal("1.00"),
    )
    profile = _profile(hard_gates=(gate,))
    registry = GateRegistry.freeze(profile=profile, common_gates=(gate,), domain_gates=())

    evaluation = registry.evaluate(
        profile=profile,
        metrics=(Metric(name="boundary", value=observed_value),),
    )

    assert evaluation.decision is decision
    assert evaluation.checks[0].decision is decision
    assert evaluation.checks[0].observed_value == observed_value
    if decision is Decision.FAIL:
        assert evaluation.reason is not None
        assert evaluation.reason.code is ErrorCode.VALIDATION


def test_ct_val_010_blocks_publication_for_gate_pbo_and_profile_failures() -> None:
    profile = _profile()
    registry = GateRegistry.freeze(
        profile=profile,
        common_gates=_COMMON_GATES,
        domain_gates=_DOMAIN_GATES,
    )
    passing_evaluation = registry.evaluate(
        profile=profile,
        metrics=(
            Metric(name="drawdown", value=Decimal("-0.05")),
            Metric(name="sharpe", value=Decimal("1.25")),
        ),
    )
    failing_evaluation = registry.evaluate(profile=profile, metrics=())
    incomplete_certificate = PboCompletenessCertificate(
        lineage_id="lineage-1",
        validation_id="validation-1",
        profile_hash=profile.profile_hash,
        search_run_hash=_OTHER_HASH,
        score_direction=RankingDirection.DESC,
        matrix_digest=None,
        decision=PboCertificateDecision.FAIL,
        reason=ErrorDetail.for_code(ErrorCode.PBO_INCOMPLETE, "missing PBO evidence"),
        started_trial_ids=(),
        terminal_trial_ids=(),
        eligible_trial_ids=(),
        matrix_trial_ids=(),
        expected_fold_ids=(),
        fold_coverage=(),
        normal_stop=False,
        minimum_eligible=1,
    )

    gate_block = publication_block_reason(
        gate_evaluation=failing_evaluation,
        pbo_certificate=_complete_certificate(),
    )
    pbo_block = publication_block_reason(
        gate_evaluation=passing_evaluation,
        pbo_certificate=incomplete_certificate,
    )
    profile_block = publication_block_reason(
        gate_evaluation=passing_evaluation,
        pbo_certificate=_complete_certificate(profile_hash=_OTHER_HASH),
    )

    assert gate_block is not None
    assert gate_block.reason == failing_evaluation.reason
    assert pbo_block is not None
    assert pbo_block.reason.code is ErrorCode.PBO_INCOMPLETE
    assert profile_block is not None
    assert profile_block.reason.code is ErrorCode.VALIDATION
    assert (
        publication_block_reason(
            gate_evaluation=passing_evaluation,
            pbo_certificate=_complete_certificate(),
        )
        is None
    )
