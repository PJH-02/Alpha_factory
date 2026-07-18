"""Profile-pinned quantized ranking for frozen parent pools and plateau tracking."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal, localcontext
from functools import cmp_to_key

from alpha_foundry.domain.models import (
    Decision,
    GateComparison,
    Metric,
    RankingDirection,
    RankingRounding,
    ValidationProfile,
)

from .models import CandidateTrial, CandidateTrialTerminal, digest_bytes

_PINNED_DECIMAL_CONTEXT = Context(
    prec=34,
    rounding=ROUND_HALF_EVEN,
    Emin=-6143,
    Emax=6144,
    capitals=1,
    clamp=1,
    flags=[],
    traps=[],
)
_ROUNDING_MODES = {
    RankingRounding.HALF_EVEN: ROUND_HALF_EVEN,
    RankingRounding.HALF_UP: ROUND_HALF_UP,
}


@dataclass(frozen=True, slots=True)
class RankedTrial:
    """An eligible trial and its profile-ordered quantized score vector."""

    trial: CandidateTrial
    quantized_scores: tuple[Decimal, ...]


def quantize_score(
    value: Decimal,
    quantization: Decimal,
    rounding: RankingRounding,
) -> Decimal:
    """Quantize a finite score in the frozen Decimal128 ranking context."""
    if not value.is_finite():
        raise ValueError("ranking scores must be finite")
    if not quantization.is_finite() or quantization <= 0:
        raise ValueError("ranking quantization must be finite and positive")
    try:
        decimal_rounding = _ROUNDING_MODES[rounding]
    except KeyError as error:
        raise ValueError(f"unsupported ranking rounding mode: {rounding}") from error
    with localcontext(_PINNED_DECIMAL_CONTEXT) as context:
        quantized = value.quantize(quantization, rounding=decimal_rounding, context=context)
    if not quantized.is_finite():
        raise ValueError("ranking score quantization must remain finite")
    return quantized


def _metric_map(metrics: tuple[Metric, ...]) -> dict[str, Decimal] | None:
    mapped: dict[str, Decimal] = {}
    for metric in metrics:
        if not metric.value.is_finite() or metric.name in mapped:
            return None
        mapped[metric.name] = metric.value
    return mapped


def _comparison_passes(comparison: GateComparison, value: Decimal, threshold: Decimal) -> bool:
    if comparison is GateComparison.LT:
        return value < threshold
    if comparison is GateComparison.LTE:
        return value <= threshold
    if comparison is GateComparison.GT:
        return value > threshold
    if comparison is GateComparison.GTE:
        return value >= threshold
    if comparison is GateComparison.EQ:
        return value == threshold
    raise ValueError(f"unsupported gate comparison: {comparison}")


def _passes_frozen_gates(
    profile: ValidationProfile,
    metric_values: dict[str, Decimal],
    trial: CandidateTrial,
) -> bool:
    evaluation = trial.evaluation
    if evaluation is None or len(evaluation.gate_results) != len(profile.hard_gates):
        return False
    for gate, result in zip(profile.hard_gates, evaluation.gate_results, strict=True):
        metric_value = metric_values.get(gate.metric_name)
        if metric_value is None or result.decision is not Decision.PASS:
            return False
        if (
            result.gate_id != gate.gate_id
            or result.metric_name != gate.metric_name
            or result.comparison != gate.comparison
            or result.threshold != gate.threshold
            or result.observed_value != metric_value
        ):
            return False
        if not _comparison_passes(gate.comparison, metric_value, gate.threshold):
            return False
    return True


def quantized_score_vector(
    profile: ValidationProfile, trial: CandidateTrial
) -> tuple[Decimal, ...] | None:
    """Return a complete eligible score vector, or ``None`` for an ineligible trial."""
    evaluation = trial.evaluation
    if (
        trial.terminal is not CandidateTrialTerminal.EVALUATED
        or evaluation is None
        or not evaluation.complete
    ):
        return None
    metric_values = _metric_map(evaluation.metrics)
    if metric_values is None or not _passes_frozen_gates(profile, metric_values, trial):
        return None
    values: list[Decimal] = []
    for rule in profile.ranking:
        value = metric_values.get(rule.metric_name)
        if value is None:
            return None
        values.append(quantize_score(value, rule.quantization, rule.rounding))
    return tuple(values)


def compare_score_vectors(
    profile: ValidationProfile,
    left: tuple[Decimal, ...],
    right: tuple[Decimal, ...],
) -> int:
    """Compare validated quantized vectors; positive means ``left`` ranks better."""
    if len(left) != len(profile.ranking) or len(right) != len(profile.ranking):
        raise ValueError("score vectors must match the frozen ranking field count")
    for rule, left_value, right_value in zip(profile.ranking, left, right, strict=True):
        if rule.direction not in (RankingDirection.ASC, RankingDirection.DESC):
            raise ValueError(f"unsupported ranking direction: {rule.direction}")
        if (
            not left_value.is_finite()
            or not right_value.is_finite()
            or quantize_score(left_value, rule.quantization, rule.rounding) != left_value
            or quantize_score(right_value, rule.quantization, rule.rounding) != right_value
        ):
            raise ValueError("score vectors must contain finite profile-quantized values")
    for rule, left_value, right_value in zip(profile.ranking, left, right, strict=True):
        if left_value == right_value:
            continue
        if rule.direction is RankingDirection.DESC:
            return 1 if left_value > right_value else -1
        return 1 if left_value < right_value else -1
    return 0


def _compare_ranked(profile: ValidationProfile, left: RankedTrial, right: RankedTrial) -> int:
    score_comparison = compare_score_vectors(profile, left.quantized_scores, right.quantized_scores)
    if score_comparison:
        return -score_comparison
    left_hash = digest_bytes(left.trial.candidate_hash)
    right_hash = digest_bytes(right.trial.candidate_hash)
    if left_hash == right_hash:
        return 0
    return -1 if left_hash < right_hash else 1


def rank_trials(
    profile: ValidationProfile, trials: Iterable[CandidateTrial]
) -> tuple[RankedTrial, ...]:
    """Rank only complete evaluated hard-gate passes using the frozen profile."""
    eligible: list[RankedTrial] = []
    for trial in trials:
        scores = quantized_score_vector(profile, trial)
        if scores is not None:
            eligible.append(RankedTrial(trial=trial, quantized_scores=scores))

    def compare_ranked(left: RankedTrial, right: RankedTrial) -> int:
        return _compare_ranked(profile, left, right)

    return tuple(sorted(eligible, key=cmp_to_key(compare_ranked)))
