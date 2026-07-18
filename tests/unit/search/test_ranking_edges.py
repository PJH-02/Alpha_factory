from __future__ import annotations

from decimal import ROUND_DOWN, Decimal, localcontext

import pytest

from alpha_foundry.domain.errors import ErrorCode, ErrorDetail
from alpha_foundry.domain.models import (
    Decision,
    Domain,
    GateComparison,
    GateResult,
    Metric,
    RankingDirection,
    RankingRounding,
    RankingRule,
    ValidationGate,
    ValidationProfile,
)
from alpha_foundry.search.models import CandidateTrial, CandidateTrialTerminal, TrialEvaluation
from alpha_foundry.search.ranking import (
    compare_score_vectors,
    quantize_score,
    quantized_score_vector,
    rank_trials,
)


def _digest(number: int) -> str:
    return f"sha256:{number:064x}"


def _profile(
    *,
    ranking_metric: str = "score",
    ranking_direction: RankingDirection = RankingDirection.DESC,
    gate_comparison: GateComparison = GateComparison.GTE,
    ranking_rounding: RankingRounding = RankingRounding.HALF_EVEN,
) -> ValidationProfile:
    return ValidationProfile(
        validation_profile_id="profile",
        domain=Domain.FACTOR,
        version="v1",
        profile_hash=_digest(20),
        hard_gates=(
            ValidationGate(
                gate_id="gate",
                metric_name="score",
                comparison=gate_comparison,
                threshold=Decimal("0"),
            ),
        ),
        ranking=(
            RankingRule(
                metric_name=ranking_metric,
                direction=ranking_direction,
                quantization=Decimal("0.01"),
                rounding=ranking_rounding,
            ),
        ),
        fold_ids=("fold-0",),
        pbo_minimum_eligible=1,
        patience=1,
        holdout_policy_hash=_digest(21),
        disclosure_policy_hash=_digest(22),
        allowed_disclosure_fields=(),
    )


def _trial(number: int, evaluation: TrialEvaluation) -> CandidateTrial:
    return CandidateTrial(
        trial_id=f"trial-{number}",
        ledger_position=number,
        candidate_hash=_digest(number),
        generation_index=0,
        slot_index=0,
        operator_id=None,
        parent_hashes=(),
        parameter_indices=(),
        terminal=evaluation.terminal,
        evaluation=evaluation,
    )


def _evaluation(
    *, metrics: tuple[Metric, ...], gate_results: tuple[GateResult, ...]
) -> TrialEvaluation:
    return TrialEvaluation(
        terminal=CandidateTrialTerminal.EVALUATED,
        metrics=metrics,
        gate_results=gate_results,
        complete=True,
        error_code=None,
    )


def _passing_gate(score: Decimal, comparison: GateComparison = GateComparison.GTE) -> GateResult:
    return GateResult(
        gate_id="gate",
        decision=Decision.PASS,
        metric_name="score",
        threshold=Decimal("0"),
        comparison=comparison,
        observed_value=score,
    )


def test_ranking_fails_closed_for_failed_or_missing_frozen_evidence() -> None:
    score = Decimal("1")
    failed_gate = _trial(
        1,
        TrialEvaluation(
            terminal=CandidateTrialTerminal.REJECTED_HARD_GATE,
            gate_results=(
                GateResult(
                    gate_id="gate",
                    decision=Decision.FAIL,
                    metric_name="score",
                    threshold=Decimal("0"),
                    comparison=GateComparison.GTE,
                    observed_value=Decimal("-1"),
                    reason=ErrorDetail.for_code(ErrorCode.VALIDATION, "hard gate failed"),
                ),
            ),
            complete=False,
            error_code="AF-GATE",
        ),
    )
    missing_gate = _trial(
        2,
        TrialEvaluation(
            terminal=CandidateTrialTerminal.REJECTED_PREFLIGHT,
            complete=False,
            error_code="AF-PREFLIGHT",
        ),
    )
    missing_ranking_metric = _trial(
        3,
        _evaluation(
            metrics=(Metric(name="score", value=score),),
            gate_results=(_passing_gate(score),),
        ),
    )
    inconsistent_pass = _trial(
        4,
        _evaluation(
            metrics=(Metric(name="score", value=Decimal("-1")),),
            gate_results=(_passing_gate(Decimal("1")),),
        ),
    )

    profile = _profile()
    assert failed_gate.evaluation is not None
    assert failed_gate.evaluation.gate_results[0].reason is not None
    assert failed_gate.evaluation.gate_results[0].reason.code is ErrorCode.VALIDATION
    assert quantized_score_vector(profile, failed_gate) is None
    assert quantized_score_vector(profile, missing_gate) is None
    assert rank_trials(profile, (failed_gate, missing_gate)) == ()
    assert quantized_score_vector(profile, inconsistent_pass) is None
    assert (
        quantized_score_vector(_profile(ranking_metric="secondary"), missing_ranking_metric) is None
    )


@pytest.mark.parametrize(
    ("value", "quantization"),
    [
        (Decimal("NaN"), Decimal("0.01")),
        (Decimal("1"), Decimal("0")),
        (Decimal("1"), Decimal("NaN")),
    ],
)
def test_quantize_score_rejects_unrankable_values(value: Decimal, quantization: Decimal) -> None:
    with pytest.raises(ValueError):
        quantize_score(value, quantization, RankingRounding.HALF_EVEN)


def test_quantize_score_uses_half_even_and_ties_break_by_raw_candidate_digest() -> None:
    profile = _profile()
    higher_digest = _trial(
        2,
        _evaluation(
            metrics=(Metric(name="score", value=Decimal("1.004")),),
            gate_results=(_passing_gate(Decimal("1.004")),),
        ),
    )
    lower_digest = _trial(
        1,
        _evaluation(
            metrics=(Metric(name="score", value=Decimal("1.001")),),
            gate_results=(_passing_gate(Decimal("1.001")),),
        ),
    )

    assert quantize_score(
        Decimal("1.005"),
        Decimal("0.01"),
        RankingRounding.HALF_EVEN,
    ) == Decimal("1.00")
    assert quantize_score(
        Decimal("1.015"),
        Decimal("0.01"),
        RankingRounding.HALF_EVEN,
    ) == Decimal("1.02")

    ranked = rank_trials(profile, (higher_digest, lower_digest))

    assert [item.trial.candidate_hash for item in ranked] == [
        lower_digest.candidate_hash,
        higher_digest.candidate_hash,
    ]
    assert [item.quantized_scores for item in ranked] == [
        (Decimal("1.00"),),
        (Decimal("1.00"),),
    ]
    assert (
        compare_score_vectors(profile, ranked[0].quantized_scores, ranked[1].quantized_scores) == 0
    )


def test_quantization_uses_profile_rounding_and_ignores_ambient_context() -> None:
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        assert quantize_score(
            Decimal("1.015"),
            Decimal("0.01"),
            RankingRounding.HALF_EVEN,
        ) == Decimal("1.02")
        assert quantize_score(
            Decimal("1.005"),
            Decimal("0.01"),
            RankingRounding.HALF_UP,
        ) == Decimal("1.01")

    score = Decimal("1.005")
    trial = _trial(
        1,
        _evaluation(
            metrics=(Metric(name="score", value=score),),
            gate_results=(_passing_gate(score),),
        ),
    )
    assert quantized_score_vector(
        _profile(ranking_rounding=RankingRounding.HALF_UP),
        trial,
    ) == (Decimal("1.01"),)


@pytest.mark.parametrize("value", (Decimal("NaN"), Decimal("Infinity"), Decimal("1.004")))
def test_score_comparison_rejects_nonfinite_or_unquantized_values(value: Decimal) -> None:
    with pytest.raises(ValueError, match="profile-quantized"):
        compare_score_vectors(_profile(), (value,), (Decimal("1.00"),))


def test_score_comparison_rejects_an_unknown_direction() -> None:
    profile = _profile()
    profile = profile.model_copy(
        update={"ranking": (profile.ranking[0].model_copy(update={"direction": "SIDEWAYS"}),)}
    )

    with pytest.raises(ValueError, match="unsupported ranking direction"):
        compare_score_vectors(profile, (Decimal("1.00"),), (Decimal("1.00"),))


def test_score_comparison_validates_later_fields_before_lexicographic_return() -> None:
    profile = _profile()
    profile = profile.model_copy(
        update={
            "ranking": (
                profile.ranking[0],
                RankingRule(
                    metric_name="secondary",
                    direction=RankingDirection.ASC,
                    quantization=Decimal("0.01"),
                ),
            )
        }
    )

    with pytest.raises(ValueError, match="profile-quantized"):
        compare_score_vectors(
            profile,
            (Decimal("2.00"), Decimal("NaN")),
            (Decimal("1.00"), Decimal("0.00")),
        )

    invalid_secondary = profile.ranking[1].model_copy(update={"direction": "SIDEWAYS"})
    invalid_profile = profile.model_copy(
        update={"ranking": (profile.ranking[0], invalid_secondary)}
    )
    with pytest.raises(ValueError, match="unsupported ranking direction"):
        compare_score_vectors(
            invalid_profile,
            (Decimal("2.00"), Decimal("0.00")),
            (Decimal("1.00"), Decimal("0.00")),
        )


def test_ranking_rejects_duplicate_and_comparison_mismatched_gate_evidence() -> None:
    score = Decimal("1")
    passing = _passing_gate(score)
    valid_evaluation = _evaluation(
        metrics=(Metric(name="score", value=score),),
        gate_results=(passing,),
    )
    duplicate_evaluation = valid_evaluation.model_copy(update={"gate_results": (passing, passing)})
    duplicate = _trial(1, valid_evaluation).model_copy(update={"evaluation": duplicate_evaluation})
    stale_evaluation = valid_evaluation.model_copy(
        update={"gate_results": (passing.model_copy(update={"comparison": GateComparison.GT}),)}
    )
    stale_comparison = _trial(2, valid_evaluation).model_copy(
        update={"evaluation": stale_evaluation}
    )

    assert quantized_score_vector(_profile(), duplicate) is None
    assert quantized_score_vector(_profile(), stale_comparison) is None


@pytest.mark.parametrize(
    ("comparison", "score"),
    (
        (GateComparison.LT, Decimal("-1")),
        (GateComparison.LTE, Decimal("0")),
        (GateComparison.GT, Decimal("1")),
        (GateComparison.GTE, Decimal("0")),
        (GateComparison.EQ, Decimal("0")),
    ),
)
def test_ranking_uses_each_frozen_hard_gate_comparison(
    comparison: GateComparison, score: Decimal
) -> None:
    profile = _profile(gate_comparison=comparison)
    trial = _trial(
        1,
        _evaluation(
            metrics=(Metric(name="score", value=score),),
            gate_results=(_passing_gate(score, comparison=comparison),),
        ),
    )

    assert quantized_score_vector(profile, trial) == (
        quantize_score(score, Decimal("0.01"), RankingRounding.HALF_EVEN),
    )


@pytest.mark.parametrize(
    ("metrics", "gate_update"),
    (
        (
            (Metric(name="secondary", value=Decimal("1")),),
            {},
        ),
        (
            (Metric(name="score", value=Decimal("1")),),
            {"gate_id": "unrecognized-gate"},
        ),
        (
            (Metric(name="score", value=Decimal("1")),),
            {"metric_name": "secondary"},
        ),
        (
            (Metric(name="score", value=Decimal("1")),),
            {"threshold": Decimal("-1")},
        ),
        (
            (Metric(name="score", value=Decimal("1")),),
            {"observed_value": Decimal("2")},
        ),
    ),
)
def test_ranking_fails_closed_for_stale_or_incomplete_hard_gate_evidence(
    metrics: tuple[Metric, ...], gate_update: dict[str, object]
) -> None:
    score = Decimal("1")
    trial = _trial(
        1,
        _evaluation(
            metrics=metrics,
            gate_results=(_passing_gate(score).model_copy(update=gate_update),),
        ),
    )

    assert quantized_score_vector(_profile(), trial) is None


def test_ascending_ranking_uses_digest_ties_only_after_score_direction() -> None:
    profile = _profile(ranking_direction=RankingDirection.ASC)
    lower_digest = _trial(
        1,
        _evaluation(
            metrics=(Metric(name="score", value=Decimal("1.004")),),
            gate_results=(_passing_gate(Decimal("1.004")),),
        ),
    )
    higher_digest = _trial(
        2,
        _evaluation(
            metrics=(Metric(name="score", value=Decimal("1.001")),),
            gate_results=(_passing_gate(Decimal("1.001")),),
        ),
    )
    worse_score = _trial(
        3,
        _evaluation(
            metrics=(Metric(name="score", value=Decimal("2")),),
            gate_results=(_passing_gate(Decimal("2")),),
        ),
    )

    ranked = rank_trials(profile, (worse_score, higher_digest, lower_digest))

    assert [item.trial.candidate_hash for item in ranked] == [
        lower_digest.candidate_hash,
        higher_digest.candidate_hash,
        worse_score.candidate_hash,
    ]
